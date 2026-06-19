'use strict';

const express = require('express');
const cookieParser = require('cookie-parser');
const cors = require('cors');
const jwt = require('jsonwebtoken');
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

// ── Health ────────────────────────────────────────────────────────────────────

app.get('/api/health', (_req, res) => {
  res.json({ ok: true });
});

// ── Student discovery (SSO stand-in: switcher populates from live DB) ─────────

// GET /api/portal/students — returns all students across tenants for the
// demo switcher. Uses the SECURITY DEFINER approach via a direct superuser
// connection is NOT available here; we read students without RLS by relying
// on portal_find_student for login but for discovery we use a simple query
// that works because keel_app owns the students table.
// Note: real SSO would supply the student identity — no endpoint like this
// would exist in production.
// portal_list_students() is a SECURITY DEFINER function (postgres-owned)
// that bypasses RLS to return all students for the demo switcher.
app.get('/api/portal/students', async (_req, res) => {
  let client;
  try {
    client = await pool.connect();
    const result = await client.query('SELECT * FROM portal_list_students()');
    res.json({ students: result.rows });
  } catch (err) {
    console.error('[students] error:', err.message);
    res.status(500).json({ error: 'Database error', detail: err.message });
  } finally {
    if (client) client.release();
  }
});

// ── Auth endpoints ────────────────────────────────────────────────────────────

// POST /api/portal/login
// portal_find_student() is a SECURITY DEFINER function owned by the postgres
// superuser — it bypasses RLS so the portal can resolve tenant_id before the
// session exists. keel_app cannot bypass RLS directly (NOBYPASSRLS). (D-P5-001)
app.post('/api/portal/login', async (req, res) => {
  const { student_id, role } = req.body;
  if (!student_id) return res.status(400).json({ error: 'student_id required' });

  const effectiveRole = role === 'registrar' ? 'registrar' : 'student';

  let tenantId;
  try {
    const client = await pool.connect();
    try {
      const result = await client.query(
        'SELECT out_student_id, out_tenant_id FROM portal_find_student($1::uuid)',
        [student_id]
      );
      if (result.rows.length === 0) {
        return res.status(404).json({ error: 'Student not found' });
      }
      tenantId = result.rows[0].out_tenant_id;
    } finally {
      client.release();
    }
  } catch (err) {
    console.error('[login] DB error:', err.message);
    return res.status(500).json({ error: 'Database error' });
  }

  setSessionCookie(res, { student_id, tenant_id: tenantId, role: effectiveRole });
  res.json({ ok: true, role: effectiveRole });
});

// POST /api/portal/logout
app.post('/api/portal/logout', (_req, res) => {
  clearSessionCookie(res);
  res.json({ ok: true });
});

// GET /api/portal/keel-token
app.get('/api/portal/keel-token', requireAuth, async (req, res) => {
  const { student_id, tenant_id } = req.session;
  try {
    // Wait for fetch to be ready (it's loaded asynchronously at startup)
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
    res.json(data); // { token, expires_in }
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
           s.id AS section_id, s.term, s.year, s.slots,
           s.course_code, c.name AS course_name, c.credits
         FROM enrollments e
         JOIN sections s ON e.section_id = s.id
         JOIN courses c ON c.code = s.course_code AND c.tenant_id = s.tenant_id
         WHERE e.student_id = $1 AND e.tenant_id = $2
         ORDER BY s.term DESC, s.course_code`,
        [student_id, tenant_id]
      )
    );
    res.json({ enrollments: result.rows });
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
        `SELECT id, student_id, type, status, payload, note, created_at, updated_at
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
    const result = await withTenantTx(client, tenant_id, (c) =>
      c.query(
        `SELECT id, actor, action, before_state, after_state, created_at
         FROM audit_log
         WHERE tenant_id = $1
         ORDER BY created_at DESC LIMIT 20`,
        [tenant_id]
      )
    );
    res.json({ activity: result.rows });
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
        `SELECT id, student_id, type, status, payload, note, created_at, updated_at
         FROM request_queue
         WHERE tenant_id = $1 AND status = $2
         ORDER BY created_at ASC`,
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
        `UPDATE request_queue SET status = $1, note = $2, updated_at = NOW()
         WHERE id = $3 AND tenant_id = $4`,
        [newStatus, note || null, id, tenant_id]
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
        `INSERT INTO audit_log (tenant_id, actor, action, before_state, after_state, created_at)
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
        `SELECT id, code, name, credits, difficulty, description
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
        `SELECT s.id, s.course_code, c.name AS course_name,
                s.term, s.year, s.slots, s.capacity, s.enrolled
         FROM sections s
         JOIN courses c ON c.code = s.course_code AND c.tenant_id = s.tenant_id
         WHERE s.tenant_id = $1
         ORDER BY s.term DESC, s.course_code`,
        [tenant_id]
      )
    );
    res.json({ sections: result.rows });
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
  console.log(`[portal] listening on :${PORT}`);
});
