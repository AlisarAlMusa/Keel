# Keel — Day 6 SPEC (Implementation Contracts)

**Goal:** three surfaces working, the widget embeddable + safely authenticated, background intelligence running. This is the contract Claude Code implements against. Read `production.md` for architecture.

> **Integration note for the implementer:** reuse the existing codebase — `Settings` (pydantic-settings), `get_session`, the Day-4 agent/chat endpoints, the engine/repositories, the SQLAlchemy models, Vault secrets, structlog, OTel/LangSmith tracing. Names below are conventions; align them to what already exists instead of duplicating. All routes are async. Every shared resource is injected with `Depends()`. New tables are Alembic migrations.

> **Boundary rule:** the **widget** is the only place a student *acts*. The **mock SIS portal** is the host (the SIS's surface). **Keel admin** configures the agent. Every write — agent or human — runs propose → validate → approve → transactional write + outbox + audit.

---

## 0. Data-model additions (Alembic migrations)

- `enrollments.source` — enum/text `('keel'|'manual'|'sis')`, default `'sis'`. Set `'keel'` on agent writes. (SIS-domain.)
- `usage_event(id, tenant_id, kind ('llm'|'embedding'), tokens int, cost_estimate numeric, created_at)` — RLS-scoped. (Keel-domain.)
- `notification(id, tenant_id, student_id, kind, payload jsonb, status ('pending'|'sent'), created_at)` — RLS-scoped. (Keel-domain.)
- Confirm existing: `requests` (SIS-domain: id, tenant_id, student_id, type, status, payload, created_at), `outbox`, `audit_log`.
- Portal sessions: **no table** — a signed, http-only cookie holds `{student_id, tenant_id}`.

---

## 1. Authentication & isolation (CRITICAL PATH)

### Token shape (JWT, HS256, secret from Vault `keel_widget_secret`)
```
{ "tenant_id": "<uuid>", "student_id": "<uuid>", "aud": "keel-widget",
  "iat": <epoch>, "exp": <iat + 900> }   # 15-min TTL
```
Signed, not encrypted → no sensitive fields beyond these.

### Endpoints — Mock portal backend (`mock_portal` module)
- `POST /portal/login` — body `{ student_id }` (from the switcher; SSO stand-in). Resolves the student's `tenant_id`, sets the signed session cookie, returns `200`.
- `POST /portal/logout` — clears the cookie.
- `GET /portal/keel-token` — **requires the session cookie.** Reads `{student_id, tenant_id}` from the session (never from the body/query). Performs the **origin check** (request `Origin` ∈ tenant `allowed_origins`). Mints the JWT. Returns `{ token, expires_in }`. The widget calls this **on chat open** (lazy).

### Dependencies — Keel backend
- `get_widget_context(authorization: str = Header) -> WidgetContext` — parse `Bearer <token>`; verify signature, `exp`, `aud == "keel-widget"`; return `WidgetContext(tenant_id, student_id)`; raise `401` on any failure. **`student_id`/`tenant_id` come only from the verified token.**
- `verify_origin(request, ctx)` — server-side check of `Origin` against the tenant's `allowed_origins`; raise `403` on mismatch. CORS/CSP are defense-in-depth, not the boundary.
- `db_with_tenant(ctx = Depends(get_widget_context), session = Depends(get_session))` — sets RLS context, yields the session, resets on teardown (see `plan.md` for the exact body). This is the dependency every student-scoped Keel route uses.

### Admin auth (separate from the widget)
- The registrar logs into Keel admin (existing fastapi-users / JWT, role `tenant_admin`). Admin routes depend on `require_role("tenant_admin")` + an RLS dependency that sets `app.tenant_id` from the **registrar's** token. Same RLS mechanism, different identity source.

### Loader
- `GET /widget.js` — served from API/MinIO with cache headers. Host pastes `<script src=".../widget.js" data-widget-id="..."></script>`; the loader injects the launcher icon, then the iframe on open.

---

## 2. Keel chat (CONSUMED, not rebuilt)
The widget calls the existing Day-4 agent/chat endpoint with the widget token + `db_with_tenant`. Streamed (SSE). No new backend here — Day 6 only adds the token + RLS wrapper around it.
- `POST /chat` (existing) — body `{ message }`; SSE stream of tokens + structured plan/approval events. Auth: `get_widget_context` + `verify_origin` + `db_with_tenant`.

---

## 3. Keel admin endpoints (agent config only — NEW)
All require `tenant_admin` + admin RLS dependency.
- `POST /admin/rag/upload` — multipart (`catalog.md`, `policy.md`, handbooks). Chunk → embed (hosted API) → write tenant-tagged pgvector rows. Returns `{ docs, chunks, last_upload }`. **Prose only; no structured rows here.**
- `GET /admin/widget-config` / `PUT /admin/widget-config` — `{ persona, allowed_origins[], enabled_tools[] }`. Safety rails are **not** in this payload (locked).
- `GET /admin/widget-snippet` — returns the `<script>` embed snippet for this tenant.
- `GET /admin/cost?period=week` — `SELECT tenant_id, kind, SUM(tokens), SUM(cost_estimate) ... GROUP BY` over `usage_event`. Returns rows for the dashboard table.
- `GET /admin/audit?limit=` — read-only Keel audit rows.

---

## 4. Mock SIS portal endpoints (NEW) — read SIS-domain tables directly
All require the **portal session cookie** (not a Keel token). Scoped to the session's student/tenant.

**Student role**
- `GET /portal/schedule` — current enrollments from SIS-domain `enrollments` joined to `sections`/`courses`, including `source` (drives the `via Keel` badge).
- `GET /portal/requests` — this student's rows from SIS-domain `requests` (status view).
- `GET /portal/activity` — recent enrollment/request changes (read view).
- Stage-set pages (section search, submit-petition form) — static; **no endpoints**.

**Registrar role** (portal session with role `registrar`)
- `GET /portal/registrar/requests?status=pending` — queue from SIS-domain `requests`.
- `POST /portal/registrar/requests/{id}/decision` — body `{ decision: 'approve'|'reject', note }`. Updates the `requests` row (transaction + outbox notify). **The one functional registrar write.**
- `GET /portal/registrar/catalog` · `/sections` · `/students` · `/rules` — read-only renders of seed data. Dead Add/Edit buttons in the UI; **no write endpoints.**

---

## 5. Background workers (NEW — RQ/Redis)
- **Auto-replan:** on a `catalog_changed` / section / prereq change event → find affected saved plans (per-tenant, scoped) → re-run audit + planner → if valid, update; else flag → outbox notify. Deduped; converge-or-flag.
- **Alerts:** evaluate exactly 4 deterministic triggers (seat opened, eligibility unlocked, risk threshold crossed, registration window opening) → write `notification` rows → outbox delivery. LLM only phrases the text.
- **Outbox publisher / email-on-seat-open:** existing pattern; retries with exponential backoff; never breaks the user-facing response.

---

## 6. Provenance & cost plumbing
- Adapter sets `enrollments.source = 'keel'` on agent writes; portal reads it for the badge.
- Every LLM/embedding call writes a `usage_event` row tagged with `tenant_id`.

---

## 7. Functional vs. non-functional
- **Functional:** widget (chat + plans + approval + enroll), token mint/verify + RLS, My Schedule, badges, registrar request queue, Keel admin (RAG/widget/cost/audit), workers.
- **Read-only-real (reads live, writes dead):** registrar Catalog/Sections/Students/Rules.
- **Stage set (visual only):** student section-search, submit-petition form.
- **Cut/skip:** manual Drop, manual Add, SIS CRUD writes, DAG editor, editable safety rails.

---

## 8. Acceptance criteria (testable)
- [ ] `POST /portal/login` → `GET /portal/keel-token` returns a JWT scoped to the session's student+tenant; body/query `student_id` is ignored.
- [ ] Token minted only on chat open (not page load).
- [ ] `get_widget_context` rejects bad signature/expiry/aud with `401`; `verify_origin` rejects a disallowed origin with `403`; a raw curl with a stale token is rejected.
- [ ] `db_with_tenant` sets and **resets** `app.tenant_id`; a Tenant-A token cannot read Tenant-B rows (RLS proven on a pooled connection).
- [ ] Admin RAG upload writes tenant-tagged pgvector chunks; structured data is seed-only.
- [ ] Student approves enrollment in the widget → `enrollments` row (`source='keel'`) + outbox + audit in one transaction; idempotency key blocks double-enroll; portal `/portal/schedule` shows it with a `via Keel` badge.
- [ ] Registrar decision updates the `requests` row + outbox; `/portal/requests` reflects the new status (direct SIS-domain read).
- [ ] Injected/unapproved request → no write of any kind.
- [ ] Portal pages never carry a Keel token; the only portal→Keel call is the token mint.