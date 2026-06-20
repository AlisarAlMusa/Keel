'use strict';

const express = require('express');
const cookieParser = require('cookie-parser');
const cors = require('cors');
const jwt = require('jsonwebtoken');
const bcrypt = require('bcryptjs');
const path = require('path');
const pool = require('./db.cjs');

// node-fetch is ESM-only in v3; use dynamic import
let fetch;
(async () => {
  const { default: f } = await import('node-fetch');
  fetch = f;
})();

const app = express();
const PORT = process.env.PORT || 3000;
const SESSION_SECRET = process.env.SESSION_SECRET || 'dev-session-secret';
const KEEL_API_URL = process.env.KEEL_API_URL || 'http://api:8000';
const PORTAL_SERVICE_SECRET = process.env.PORTAL_SERVICE_SECRET || 'dev-service-secret';
// PORTAL_TENANT: slug of the tenant this portal instance is bound to (spec §S9, §P11)
const PORTAL_TENANT = process.env.PORTAL_TENANT || 'northane';
const SESSION_COOKIE = 'keel_session';
const SESSION_TTL_SECONDS = 8 * 60 * 60; // 8 hours

// ── Middleware ────────────────────────────────────────────────────────────────

app.use(cors({ origin: true, credentials: true }));
app.use(express.json());
app.use(cookieParser());

// ── Session helpers ───────────────────────────────────────────────────────────

function signSession(payload) {
  return jwt.sign(payload, SESSION_SECRET, {
    algorithm: 'HS256',
    expiresIn: SESSION_TTL_SECONDS,
  });
}

function verifySession(token) {
  try {
    return jwt.verify(token, SESSION_SECRET, { algorithms: ['HS256'] });
  } catch {
    return null;
  }
}

function setSessionCookie(res, payload) {
  const token = signSession(payload);
  res.cookie(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: 'lax',
    maxAge: SESSION_TTL_SECONDS * 1000,
    // secure: true in prod — left to reverse-proxy/TLS layer
  });
  return token;
}

function clearSessionCookie(res) {
  res.clearCookie(SESSION_COOKIE, { httpOnly: true, sameSite: 'lax' });
}

// ── Auth middleware ───────────────────────────────────────────────────────────

function requireAuth(req, res, next) {
  const token = req.cookies && req.cookies[SESSION_COOKIE];
  if (!token) return res.status(401).json({ error: 'Not authenticated' });
  const session = verifySession(token);
  if (!session) return res.status(401).json({ error: 'Session expired or invalid' });
  req.session = session;
  next();
}

function requireRegistrar(req, res, next) {
  requireAuth(req, res, () => {
    if (req.session.role !== 'registrar') {
      return res.status(403).json({ error: 'Registrar access required' });
    }
    next();
  });
}

// ── RLS helper ────────────────────────────────────────────────────────────────
// set_config with is_local=true is transaction-scoped: it only persists until
// the end of the current transaction.  Always call inside BEGIN…COMMIT so the
// setting is visible to the query that follows.

async function withTenantTx(client, tenantId, fn) {
  await client.query('BEGIN');
  await client.query("SELECT set_config('app.tenant_id', $1, true)", [String(tenantId)]);
  try {
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    throw e;
  }
}

// ── Tenant suspension check ───────────────────────────────────────────────────
// Checks the Keel tenants table (no RLS on tenants).
// Returns true if the tenant is active; false if suspended/missing.

async function isTenantActive(tenantSlug) {
  let client;
  try {
    client = await pool.connect();
    const result = await client.query(
      "SELECT status FROM tenants WHERE slug = $1",
      [tenantSlug]
    );
    if (result.rows.length === 0) return false;
    return result.rows[0].status === 'active';
  } catch (err) {
    console.error('[suspend_check] DB error:', err.message);
    return false; // fail-closed
  } finally {
    if (client) client.release();
  }
}

// ── Health ────────────────────────────────────────────────────────────────────

app.get('/api/health', (_req, res) => {
  res.json({ ok: true, portal_tenant: PORTAL_TENANT });
});

// ── Student discovery — legacy switcher (kept for backward compat in dev) ─────
// In production the switcher is removed; only email+password login is available.
// This endpoint is still useful for development/testing.

app.get('/api/portal/students', async (_req, res) => {
  let client;
  try {
    client = await pool.connect();
    const result = await client.query('SELECT * FROM portal_list_students()');
    // Filter to this portal's tenant
    const tenantStudents = result.rows.filter(s => s.tenant_slug === PORTAL_TENANT);
    res.json({ students: tenantStudents });
  } catch (err) {
    console.error('[students] error:', err.message);
    res.status(500).json({ error: 'Database error', detail: err.message });
  } finally {
    if (client) client.release();
  }
});

// ── Auth endpoints ────────────────────────────────────────────────────────────

// POST /api/portal/login — real email + password (spec §S8)
//
// 1. Look up portal_user by unique email (SECURITY DEFINER — pre-tenant bootstrap)
// 2. Verify bcrypt password (generic 401 on failure — no user enumeration)
// 3. Assert record.tenant_id == this portal instance's tenant (403 if wrong portal)
// 4. No suspend check here — portal login is a SIS surface; students still log in
//    even when Keel is suspended (spec §S8 + §S11 acceptance criteria)
// 5. Set signed http-only session cookie {student_id, tenant_id, role}

app.post('/api/portal/login', async (req, res) => {
  const { email, password } = req.body;
  if (!email || !password) {
    return res.status(400).json({ error: 'email and password required' });
  }

  let client;
  try {
    client = await pool.connect();
    // portal_find_by_email is a SECURITY DEFINER function — bypasses RLS for
    // the pre-session lookup (keel_app is NOBYPASSRLS).
    const result = await client.query(
      'SELECT user_id, tenant_id, role, hashed_password, student_id FROM portal_find_by_email($1)',
      [email]
    );

    // Generic 401 for any auth failure — never leak "email not found"
    if (result.rows.length === 0) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    const user = result.rows[0];

    const pwValid = await bcrypt.compare(password, user.hashed_password);
    if (!pwValid) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    // Resolve this portal's tenant_id from PORTAL_TENANT slug
    const tenantRow = await client.query(
      "SELECT id, slug FROM tenants WHERE slug = $1",
      [PORTAL_TENANT]
    );
    if (tenantRow.rows.length === 0) {
      console.error('[login] PORTAL_TENANT not found in DB:', PORTAL_TENANT);
      return res.status(500).json({ error: 'Portal misconfigured' });
    }
    const portalTenantId = tenantRow.rows[0].id;

    // Tenant-match: ensure this account belongs to this portal instance (spec §S8 step 3)
    if (String(user.tenant_id) !== String(portalTenantId)) {
      console.warn('[login] cross-portal attempt', { email, expected: portalTenantId, got: user.tenant_id });
      return res.status(403).json({ error: 'This account does not belong to this portal' });
    }

    // Set session — student_id is from the portal_user's student link (or null for registrar)
    setSessionCookie(res, {
      student_id: user.student_id || user.user_id,  // fallback to user_id for registrar
      tenant_id: user.tenant_id,
      role: user.role,
    });
    res.json({ ok: true, role: user.role });
  } catch (err) {
    console.error('[login] error:', err.message);
    res.status(500).json({ error: 'Database error' });
  } finally {
    if (client) client.release();
  }
});

// POST /api/portal/logout
app.post('/api/portal/logout', (_req, res) => {
  clearSessionCookie(res);
  res.json({ ok: true });
});

// GET /api/portal/keel-token
// Unchanged contract (spec §S8): reads {student_id, tenant_id} from session.
// Adds suspend gate: if tenant is suspended → 403 (widget goes dark).
// /portal/login does NOT do the suspend check — SIS stays up even when Keel is suspended.
app.get('/api/portal/keel-token', requireAuth, async (req, res) => {
  const { student_id, tenant_id } = req.session;

  // Suspend gate — Keel goes dark, SIS portal stays up (spec §S1, §S8)
  const active = await isTenantActive(PORTAL_TENANT);
  if (!active) {
    return res.status(403).json({
      error: 'Advising assistant unavailable for your institution',
      code: 'TENANT_SUSPENDED',
    });
  }

  try {
    // Wait for fetch to be ready (loaded asynchronously at startup)
    let attempts = 0;
    while (!fetch && attempts < 20) {
      await new Promise((r) => setTimeout(r, 50));
      attempts++;
    }
    if (!fetch) return res.status(503).json({ error: 'fetch not ready' });

    const keelRes = await fetch(`${KEEL_API_URL}/internal/mint-token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${PORTAL_SERVICE_SECRET}`,
      },
      body: JSON.stringify({ tenant_id, student_id }),
    });

    if (!keelRes.ok) {
      const text = await keelRes.text();
      console.error('[keel-token] keel-api error:', keelRes.status, text);
      return res.status(502).json({ error: 'Failed to mint widget token' });
    }

    const data = await keelRes.json();
    res.json(data); // { token, expires_in, persona_name }
  } catch (err) {
    console.error('[keel-token] error:', err);
    res.status(500).json({ error: 'Internal error minting token' });
  }
});

// ── Student endpoints ─────────────────────────────────────────────────────────

// GET /api/portal/schedule
app.get('/api/portal/schedule', requireAuth, async (req, res) => {
  const { student_id, tenant_id } = req.session;
  const client = await pool.connect();
  try {
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT
           e.id, e.student_id, e.status, e.source,
           s.id AS section_id,
           ROW_NUMBER() OVER (PARTITION BY s.course_code ORDER BY s.id) AS section_num,
           s.term, s.year, s.slots,
           s.course_code, c.name AS course_title, c.credits
         FROM enrollments e
         JOIN sections s ON e.section_id = s.id
         JOIN courses c ON c.code = s.course_code AND c.tenant_id = s.tenant_id
         WHERE e.student_id = $1 AND e.tenant_id = $2
         ORDER BY s.term DESC, s.course_code`,
        [student_id, tenant_id]
      )
    );

    // Transform slots JSONB → human-readable days/start_time/end_time
    const DAY_MAP = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };
    const fmtMin = (m) => {
      const h = Math.floor(m / 60);
      const min = m % 60;
      const ampm = h < 12 ? 'AM' : 'PM';
      const h12 = h % 12 || 12;
      return `${h12}:${String(min).padStart(2, '0')} ${ampm}`;
    };

    const enrollments = result.rows.map((row) => {
      const slots = Array.isArray(row.slots) ? row.slots : [];
      const days = [...new Set(slots.map((s) => DAY_MAP[s.day] || s.day))].join('/');
      const starts = slots.map((s) => s.start_min);
      const ends = slots.map((s) => s.end_min);
      const start_time = starts.length ? fmtMin(Math.min(...starts)) : null;
      const end_time = ends.length ? fmtMin(Math.max(...ends)) : null;
      return {
        ...row,
        section_num: Number(row.section_num),
        days: days || null,
        start_time,
        end_time,
        slots: undefined, // don't expose raw JSONB
      };
    });

    res.json({ enrollments });
  } catch (err) {
    console.error('[schedule] error:', err);
    res.status(500).json({ error: 'Database error fetching schedule' });
  } finally {
    client.release();
  }
});

// GET /api/portal/requests
app.get('/api/portal/requests', requireAuth, async (req, res) => {
  const { student_id, tenant_id } = req.session;
  const client = await pool.connect();
  try {
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT id, student_id, type, status, payload, created_at, resolved_at, target
         FROM request_queue
         WHERE student_id = $1 AND tenant_id = $2
         ORDER BY created_at DESC`,
        [student_id, tenant_id]
      )
    );
    res.json({ requests: result.rows });
  } catch (err) {
    console.error('[requests] error:', err);
    res.status(500).json({ error: 'Database error fetching requests' });
  } finally {
    client.release();
  }
});

// GET /api/portal/activity
app.get('/api/portal/activity', requireAuth, async (req, res) => {
  const { tenant_id } = req.session;
  const client = await pool.connect();
  try {
    // Step 1: get activity rows (RLS-scoped to this tenant)
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT id, actor, action, before, after, created_at
         FROM audit_log
         WHERE tenant_id = $1
         ORDER BY created_at DESC LIMIT 20`,
        [tenant_id]
      )
    );
    const rows = result.rows;

    // Step 2: resolve actor UUIDs → names (run inside tenant tx so RLS is satisfied)
    const actorIds = [...new Set(rows.map((r) => r.actor).filter((a) => a && a.length === 36))];
    const nameMap = {};
    if (actorIds.length) {
      const nameResult = await withTenantTx(client, tenant_id, (c) =>
        c.query(
          `SELECT student_id::text AS id, email
           FROM portal_user
           WHERE tenant_id = $1 AND role = 'student' AND student_id::text = ANY($2)`,
          [tenant_id, actorIds]
        )
      );
      for (const row of nameResult.rows) {
        nameMap[row.id] = row.email;
      }
    }

    const activity = rows.map((r) => {
      const email = nameMap[r.actor];
      return {
        ...r,
        actor_email: email || null,
        actor_name: email ? email.split('@')[0].replace(/[._-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : null,
      };
    });

    res.json({ activity });
  } catch (err) {
    console.error('[activity] error:', err);
    res.status(500).json({ error: 'Database error fetching activity' });
  } finally {
    client.release();
  }
});

// ── Registrar endpoints ───────────────────────────────────────────────────────

// GET /api/portal/registrar/requests?status=pending
app.get('/api/portal/registrar/requests', requireRegistrar, async (req, res) => {
  const { tenant_id } = req.session;
  const status = req.query.status || 'pending';
  const client = await pool.connect();
  try {
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT rq.id, rq.student_id, rq.type, rq.status, rq.payload,
                rq.created_at, rq.resolved_at, rq.target,
                pu.email AS student_email,
                initcap(split_part(pu.email, '@', 1)) AS student_name
         FROM request_queue rq
         LEFT JOIN portal_user pu ON pu.student_id = rq.student_id
                   AND pu.tenant_id = rq.tenant_id AND pu.role = 'student'
         WHERE rq.tenant_id = $1 AND rq.status = $2
         ORDER BY rq.created_at ASC`,
        [tenant_id, status]
      )
    );
    res.json({ requests: result.rows });
  } catch (err) {
    console.error('[registrar/requests] error:', err);
    res.status(500).json({ error: 'Database error fetching request queue' });
  } finally {
    client.release();
  }
});

// POST /api/portal/registrar/requests/:id/decision
app.post('/api/portal/registrar/requests/:id/decision', requireRegistrar, async (req, res) => {
  const { tenant_id, student_id: actorId } = req.session;
  const { id } = req.params;
  const { decision, note } = req.body;

  if (!['approve', 'reject'].includes(decision)) {
    return res.status(400).json({ error: 'decision must be approve or reject' });
  }

  const newStatus = decision === 'approve' ? 'approved' : 'rejected';
  const client = await pool.connect();
  try {
    await withTenantTx(client, tenant_id, async (c) => {
      const fetchResult = await c.query(
        'SELECT * FROM request_queue WHERE id = $1 AND tenant_id = $2',
        [id, tenant_id]
      );
      if (fetchResult.rows.length === 0) {
        const err = new Error('not_found');
        err.code = 'NOT_FOUND';
        throw err;
      }
      const requestRow = fetchResult.rows[0];

      await c.query(
        `UPDATE request_queue SET status = $1, resolved_at = NOW()
         WHERE id = $2 AND tenant_id = $3`,
        [newStatus, id, tenant_id]
      );
      await c.query(
        `INSERT INTO outbox (tenant_id, event_type, payload, created_at) VALUES ($1, $2, $3, NOW())`,
        [
          tenant_id,
          `request.${newStatus}`,
          JSON.stringify({ request_id: id, student_id: requestRow.student_id, type: requestRow.type, status: newStatus, note: note || null }),
        ]
      );
      await c.query(
        `INSERT INTO audit_log (tenant_id, actor, action, before, after, created_at)
         VALUES ($1, $2, $3, $4, $5, NOW())`,
        [
          tenant_id,
          actorId || 'registrar',
          `request_queue.${newStatus}`,
          JSON.stringify({ status: requestRow.status }),
          JSON.stringify({ status: newStatus, note: note || null }),
        ]
      );
    });
    res.json({ id, status: newStatus, note: note || null });
  } catch (err) {
    if (err.code === 'NOT_FOUND') return res.status(404).json({ error: 'Request not found' });
    console.error('[registrar/decision] error:', err);
    res.status(500).json({ error: 'Database error processing decision' });
  } finally {
    client.release();
  }
});

// GET /api/portal/registrar/catalog
app.get('/api/portal/registrar/catalog', requireRegistrar, async (req, res) => {
  const { tenant_id } = req.session;
  const client = await pool.connect();
  try {
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT id, code, name AS title, credits, description,
                NULL::text AS department
         FROM courses WHERE tenant_id = $1 ORDER BY code`,
        [tenant_id]
      )
    );
    res.json({ courses: result.rows });
  } catch (err) {
    console.error('[registrar/catalog] error:', err);
    res.status(500).json({ error: 'Database error fetching catalog' });
  } finally {
    client.release();
  }
});

// GET /api/portal/registrar/sections
app.get('/api/portal/registrar/sections', requireRegistrar, async (req, res) => {
  const { tenant_id } = req.session;
  const client = await pool.connect();
  try {
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT s.id, s.course_code, c.name AS course_title,
                ROW_NUMBER() OVER (PARTITION BY s.course_code ORDER BY s.id) AS section_num,
                s.term, s.year, s.slots, s.capacity, s.enrolled
         FROM sections s
         JOIN courses c ON c.code = s.course_code AND c.tenant_id = s.tenant_id
         WHERE s.tenant_id = $1
         ORDER BY s.term DESC, s.course_code`,
        [tenant_id]
      )
    );

    const DAY_MAP2 = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };
    const fmtMin2 = (m) => {
      const h = Math.floor(m / 60), min = m % 60;
      const h12 = h % 12 || 12, ampm = h < 12 ? 'AM' : 'PM';
      return `${h12}:${String(min).padStart(2,'0')} ${ampm}`;
    };

    const sections = result.rows.map((row) => {
      const slots = Array.isArray(row.slots) ? row.slots : [];
      const days = [...new Set(slots.map((s) => DAY_MAP2[s.day] || s.day))].join('/');
      const starts = slots.map((s) => s.start_min), ends = slots.map((s) => s.end_min);
      return {
        id: row.id,
        course_code: row.course_code,
        course_title: row.course_title,
        section_num: Number(row.section_num),
        term: row.term,
        year: row.year,
        days: days || null,
        start_time: starts.length ? fmtMin2(Math.min(...starts)) : null,
        end_time: ends.length ? fmtMin2(Math.max(...ends)) : null,
        instructor: null,
        capacity: row.capacity,
        enrolled: row.enrolled,
      };
    });
    res.json({ sections });
  } catch (err) {
    console.error('[registrar/sections] error:', err);
    res.status(500).json({ error: 'Database error fetching sections' });
  } finally {
    client.release();
  }
});

// GET /api/portal/registrar/students
app.get('/api/portal/registrar/students', requireRegistrar, async (req, res) => {
  const { tenant_id } = req.session;
  const client = await pool.connect();
  try {
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT s.id, u.display_name AS name, u.email, s.program_code, s.current_year, s.has_hold
         FROM students s
         JOIN users u ON u.id = s.user_id
         WHERE s.tenant_id = $1
         ORDER BY u.display_name`,
        [tenant_id]
      )
    );
    res.json({ students: result.rows });
  } catch (err) {
    console.error('[registrar/students] error:', err);
    res.status(500).json({ error: 'Database error fetching students' });
  } finally {
    client.release();
  }
});

// GET /api/portal/registrar/rules
app.get('/api/portal/registrar/rules', requireRegistrar, async (req, res) => {
  const { tenant_id } = req.session;
  const client = await pool.connect();
  try {
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT id, program_code, group_name, required_credits, eligible_course_codes
         FROM program_requirements
         WHERE tenant_id = $1
         ORDER BY program_code, group_name`,
        [tenant_id]
      )
    );
    res.json({ rules: result.rows });
  } catch (err) {
    console.error('[registrar/rules] error:', err);
    res.status(500).json({ error: 'Database error fetching rules' });
  } finally {
    client.release();
  }
});

// ── Serve React SPA (production) ──────────────────────────────────────────────

if (process.env.NODE_ENV === 'production') {
  const distPath = path.join(__dirname, '..', 'dist');
  app.use(express.static(distPath));
  // Client-side routing fallback
  app.get('*', (_req, res) => {
    res.sendFile(path.join(distPath, 'index.html'));
  });
}

// ── Start ─────────────────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(`[portal:${PORTAL_TENANT}] listening on :${PORT}`);
});
