# Keel — Day 6 PLAN (Implementation Patterns & Order)

How to build Day 6. `spec.md` = contracts; `docs/PRODUCTION.md` = architecture; this = the *how*, with pseudocode for the load-bearing parts. Code is illustrative — match it to the existing codebase.

---

## 1. Build order (critical path first)
1. **Widget auth: token mint → verify → RLS dependency → origin check.** Test cross-tenant the same day.
2. **`/widget.js` loader + lazy mint on chat open.**
3. **Student widget UI** (chat, plan view, badges, save/compare/activate, approval).
4. **Keel admin** (RAG upload, widget config, cost, audit).
5. **Mock SIS portal** — student role, then registrar role.
6. **Workers** — auto-replan, alerts.

---

## 2. Auth — implementation patterns

### 2a. Mint (mock portal backend)
```python
@router.get("/portal/keel-token")
async def keel_token(request: Request, session=Depends(portal_session)):
    # session came from the signed cookie set by /portal/login
    verify_origin_or_403(request, session.tenant_id)        # defense in depth
    now = int(time.time())
    payload = {"tenant_id": str(session.tenant_id),
               "student_id": str(session.student_id),
               "aud": "keel-widget", "iat": now, "exp": now + 900}
    token = jwt.encode(payload, settings.keel_widget_secret, algorithm="HS256")
    return {"token": token, "expires_in": 900}
```
`/portal/login` resolves the student → tenant and writes the signed cookie. The widget never sends `student_id`.

### 2b. Verify (Keel backend dependency)
```python
async def get_widget_context(authorization: str = Header(...)) -> WidgetContext:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(token, settings.keel_widget_secret,
                            algorithms=["HS256"], audience="keel-widget")
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token")
    return WidgetContext(tenant_id=claims["tenant_id"], student_id=claims["student_id"])
```

### 2c. RLS-context dependency (the isolation enforcer)
```python
async def db_with_tenant(
    ctx: WidgetContext = Depends(get_widget_context),
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[AsyncSession]:
    # set_config(..., is_local=true) scopes to the current transaction and
    # auto-resets at commit/rollback; the explicit reset is belt-and-suspenders
    # for pooled connections in autocommit.
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(ctx.tenant_id)},
    )
    try:
        yield session
    finally:
        await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
```
The existing RLS POLICY (Day 1) reads `current_setting('app.tenant_id')`. **Every student-scoped Keel route depends on `db_with_tenant`.** Admin routes use the analogous dependency that sets the variable from the registrar's token.

### 2d. Origin check
```python
def verify_origin_or_403(request: Request, tenant_id) -> None:
    origin = request.headers.get("origin")
    if origin not in allowed_origins_for(tenant_id):
        raise HTTPException(403, "origin not allowed")
```
Applied at mint and on every chat request. CORS/CSP middleware is configured from the same `allowed_origins`, as depth only.

### 2e. Lazy mint (widget)
- Page load: loader injects the launcher icon — no token.
- On icon click (chat opens): `GET /portal/keel-token` → store token in memory (not localStorage).
- On `401`/expiry mid-session: silently re-fetch and retry once.

---

## 3. Mock SIS portal
- Lives as a `mock_portal` module (same FastAPI app is fine; logically the SIS surface).
- **Reads SIS-domain tables directly** under the portal session — never via Keel routes.
- Student endpoints (`/portal/schedule`, `/portal/requests`, `/portal/activity`) query `enrollments`/`sections`/`courses`/`requests`, scoped to the session student + RLS tenant.
- Registrar `decision` endpoint = the action pattern (transaction + outbox).
- Registrar Catalog/Sections/Students/Rules endpoints are plain reads of seed data; the UI buttons are dead.

---

## 4. Keel admin
- RAG upload: parse files → chunk (justify size/overlap in docs/DECISIONS.md) → embed via hosted API → insert tenant-tagged pgvector rows. Reuse the Day-3 RAG pipeline.
- Widget config: persist `{persona, allowed_origins, enabled_tools}`; safety rails untouched (locked in code).
- Cost: `GROUP BY tenant_id, kind` over `usage_event`.
- Audit: read-only render.

---

## 5. One action pattern, reused
```
propose → engine.validate() → human approve → async with session.begin():
    write domain row (enrollment: source='keel' | requests: status=...)
    write outbox event
→ worker publishes (email/audit) ; idempotency key prevents duplicates ; audit_log row
```
Agent enrollment → SIS-domain `enrollments`. Registrar decision → SIS-domain `requests`. Both identical shape.

---

## 6. Workers (RQ/Redis)
- Auto-replan: subscribe to catalog/section/prereq change → query affected saved plans (per-tenant) → re-run audit+planner → update or flag → outbox. Dedupe by (plan_id, change_id).
- Alerts: scheduled scan of the 4 triggers → `notification` rows → outbox. LLM phrases only; triggers are pure rules.
- Retries: tenacity exponential backoff; structured logs; never block the request path.

---

## 7. Adapter (document only this week)
- Do **not** refactor the data layer pre-demo. The `source='keel'` write goes through whatever repository writes enrollments today; name that boundary the SIS write path.
- Capture the `SISGateway` interface + `sis_integration` per-tenant config in `docs/PRODUCTION.md`. Post-demo: extract the interface, add a `LocalPostgresSIS` impl. BUT THIS IS NOT NOW, POST DEMO

---

## 8. Risks
- **Auth is the silent time sink** — if token/origin/RLS isn't airtight, isolation (the grade) fails. Prove cross-tenant on a **pooled** connection on Day 6.
- **RLS variable leak** — reset per request; the test in §I of tasks proves it.
- **Day 6 overload** — registrar read-only views and the workers are the relief valves.

---

## 9. Cut order (if behind)
1. Registrar Students/Rules views (keep Catalog + Sections).
2. Cost dashboard UI (keep writing `usage_event`).
3. Auto-replan worker.
4. Student stage-set pages (keep My Schedule + badges).
**Never cut:** auth boundary · approval gate · My Schedule write-proof · registrar request queue · guardrails.

---

## 10. Addendum build order — operator, auth, second portal

> Merged from the former `missing_recovery.md` PLAN section. Contracts: spec.md §10;
> rationale: DECISIONS D-A-001…005, D-R-008/011/015.

1. **Migrations** — `tenants.status`; nullable `users.tenant_id` + `'platform_operator'` role;
   check constraints; `platform_audit`; `platform_usage_summary`; `portal_user`.
2. **Auth** — role-stamped token issue (operator token has **no** `tenant_id`);
   `require_role("platform_operator")`; `assert_tenant_active` wired into `/internal/mint-token`,
   `/chat`, and Keel admin login the same day.
3. **Operator endpoints** — tenants list / provision / suspend / unsuspend / erase(enqueue) /
   cost / audit. Router carries `Depends(require_role("platform_operator"))` and uses
   `get_session` directly — **never** `db_with_tenant`, never a tenant-content repository.
4. **Erase worker** — `erase_tenant` RQ job: cascade delete-by-`tenant_id` (Keel + demo SIS) →
   admins → MinIO prefix → tenant row → audit counts. Idempotent (missing tenant = no-op).
5. **Portal auth** — `POST /portal/login {email,password}` (bcrypt, tenant-bound to
   `PORTAL_TENANT`, generic 401); `/portal/keel-token` unchanged except the suspend gate.
6. **Second portal** — one image, two compose services (Northane :3001, Summit :3002),
   differing only by env; each tenant's `allowed_origins` lists its own portal origin.
7. **Platform Console UI** — same shell/stack as the admin console, role-gated to the operator.

**Risks:** operator leaking into tenant scope is the whole grade — mitigate structurally (no
`tenant_id`, no content repo under `/platform`) and prove with a CI gate, not code review; keep
the cross-tenant cost read confined to the one `SECURITY DEFINER` aggregate function; erase is
destructive → confirmation + async + idempotent + audit, with suspend as the reversible default.

## 11. Section-selection flow — build notes

> Merged from the former `registration-section-flow.md`. Contract: spec.md §11; rationale:
> DECISIONS D-P6-001/002.

- Mirror the plan loop: engine returns the **open-section pool**; the LLM proposes a
  preference-fitting combination; the engine re-verifies (open + conflict-free + right course +
  eligible) before staging. Reuse `domain/engine/sections.py::find_sections` for the
  conflict-free logic; one shared section-time formatting path.
- Route a failed stage tool (a `ToolError` with no `action_id`) **back to the LLM** (the
  `_after_stage` conditional edge in `agent/graph.py`) so the student hears "that section is full
  — want the waitlist or another term?" instead of a silent loop.
- System-prompt rule: on a tool error, tell the student plainly and offer the waitlist / another
  term; never fabricate a result.