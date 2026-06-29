# Keel — Day 6 ADDENDUM: Platform Operator · Admin/Operator Auth · Second Tenant Portal + Portal Student Auth

> **What this file is.** A self-contained work order covering three gaps missed in Day 6: (1) the **platform operator** role, (2) **auth for admin and operator**, and (3) a **second tenant portal (Summit)** with **real portal student login (email + password)** so cross-tenant isolation is actually demonstrable. It is written to be implemented as-is and then folded into the Day-6 docs.
>
> **How Claude Code must use it.**
> 1. **Implement** everything in the SPEC + PLAN + TASKS sections below.
> 2. **Then merge** each section into its Day-6 doc: SPEC → `spec.md`, PLAN → `plan.md`, TASKS → `tasks.md`. Add as new numbered subsections; keep existing numbering intact; do not duplicate anything already there.
> 3. Update `production.md` §0 decisions log + the role table to include the operator. Paste the Decisions block below into `DECISIONS.md`.
> 4. **Align to what already exists.** Reuse: the `tenants` model, the fastapi-users `users` model + role field, `audit_log`, `outbox`, the RQ worker, `Settings` (pydantic-settings), `get_session`, Vault, structlog, the admin RLS dependency, and the existing `frontend.md` conventions. Names below are conventions — match them to the real codebase, don't fork it.
>
> **Hard boundaries (do not violate).**
> - **Do not build `sis_integration`** — it stays documented-only / post-demo. Provisioning creates a Keel tenant shell + admin only; no adapter config.
> - **The operator never reads tenant content.** No operator endpoint may query a tenant-content table for rows. Operator touches platform-domain tables + aggregate-only functions only. This is enforced structurally (no `tenant_id` on the operator identity) and by a mandatory CI gate.
> - **Portal student auth is portal-domain, NOT Keel.** The portal authenticates the student (the SSO stand-in). Keel never sees a student password and never authenticates a student — it only receives the server-side-minted widget token. Student credentials live in a portal/SIS-domain table, never in Keel's `users` table.
> - **Second portal = one image, two instances.** Do **not** fork the portal frontend. Run the same image twice (Northane + Summit) on two origins, differentiated only by env. The two origins are what make the Keel origin-check demonstrable.

---

## Decisions (paste into DECISIONS.md)

- **Third role restored: platform operator.** Powers (from the brief): provision · suspend · erase tenants. Identity carries **no `tenant_id`**, so it cannot satisfy the RLS predicate for any tenant-content read. Operator endpoints only touch platform-domain tables and aggregate functions. "Controlled doorway, not god mode."
- **Auth = email + password for both admin and operator.** A tenant-id is *identification*, not *authentication* — it's semi-public (it ships in the widget snippet / `allowed_origins`), gives no per-person accountability, and can't distinguish multiple staff. Tenant-id is a **JWT claim derived from the authenticated account**, never a credential.
- **One users table, role enum `{tenant_admin, platform_operator}`.** DB check constraint makes the isolation invariant a database fact: `platform_operator ⇒ tenant_id IS NULL` and `tenant_admin ⇒ tenant_id IS NOT NULL`.
- **Operator cost = aggregate billing metadata only, not content.** `usage_event` holds no content (only `tenant_id, kind, tokens, cost_estimate, created_at`). Cross-tenant cost is read through a single `SECURITY DEFINER` aggregate function that can only return grouped numbers. Operator sees *that* a tenant spent X, never *what* on.
- **Erase is async, confirmation-gated, and idempotent.** Suspend (reversible) is the safe default; erase is a worker job that cascade-deletes by `tenant_id`, returns counts, never content. `platform_audit` (separate, non-tenant-scoped) survives the erase so the operator's action is permanently recorded.
- **Demo vs prod for erase.** Demo is one Postgres → erase cascades both Keel-domain and the local SIS-domain seed rows for that tenant. Production would erase Keel-domain only + signal SIS deprovision (out of scope).
- **Seed is idempotent.** `seed.py` upserts the operator account + one admin per demo tenant. Rerun = complete and in sync.
- **Portal login is real email + password, in the portal domain.** Replaces the `student_id` switcher. Realistic emails (`alisar@northane.edu`, `…@summit.edu`). Credentials live in a portal-domain `portal_user` table (role `student|registrar`), hashed (argon2/bcrypt). Login looks up by unique email (the pre-tenant bootstrap query, same shape as the Keel admin login), then the session carries `tenant_id` and RLS governs every read after. The `/portal/keel-token` mint is unchanged — it still reads `{student_id, tenant_id}` from the session, never the client.
- **Second tenant (Summit) is a second instance of one portal image.** Two compose services (`portal-northane`, `portal-summit`) from the same Dockerfile, differing only by env (tenant slug, port/origin, widget snippet, branding). Two origins make the origin-check and cross-tenant story demonstrable; one codebase avoids sprawl and means Summit "follows Northane" by construction.
- **Each portal instance is bound to its tenant.** It authenticates only that tenant's accounts (login asserts record-tenant == instance-tenant). A Northane student cannot log into the Summit portal. Tenant is bound twice: at the portal (login match) and at Keel (origin check).
- **Suspension darkens Keel, not the SIS portal.** The operator suspends a Keel *tenant* (the university's subscription to the advising layer). The gate fires at the Keel boundary — `/portal/keel-token` mint, `/chat`, Keel admin — so the widget stops working. It does **not** fire at `/portal/login` or `/portal/*` SIS reads: students still log into their university's portal and see My Schedule, the registrar still works the queue. Keel has no authority over the SIS. (Erase is different — it removes the `portal_user` rows too, so login then fails naturally.)

---

# SPEC section  (→ append to `spec.md`)

## S0. Data-model additions (Alembic migrations)

- **`tenants.status`** — enum/text `('active'|'suspended')`, default `'active'`. (Platform-domain.) Add a transient `'erasing'` value only if you mark before the async erase; otherwise erase deletes the row directly.
- **`users.tenant_id`** — make **nullable** (operators have none).
- **`users.role`** — ensure value `'platform_operator'` exists. If `role` is a native PG enum: `ALTER TYPE ... ADD VALUE 'platform_operator'` (run via `op.execute` outside a txn block — Postgres forbids new enum values inside a transaction). If `role` is text, no migration needed.
- **`users` check constraints** (one migration):
  - `ck_operator_no_tenant`: `(role <> 'platform_operator') OR (tenant_id IS NULL)`
  - `ck_admin_has_tenant`:  `(role <> 'tenant_admin') OR (tenant_id IS NOT NULL)`
- **`platform_audit`** — **platform-domain, NOT RLS-scoped.** `(id, actor_user_id, action ('provision'|'suspend'|'unsuspend'|'erase'), target_tenant_id nullable, detail jsonb, created_at)`. Survives tenant erase by design.
- **`platform_usage_summary(p_period text)`** — `SECURITY DEFINER` SQL function returning aggregate rows only (see S4). Grant `EXECUTE` to the app role.
- **`portal_user`** — **SIS/portal-domain, RLS-scoped by `tenant_id`.** `(id, tenant_id, role ('student'|'registrar'), email unique, hashed_password, student_id nullable FK, created_at)`. Holds portal login credentials (see S8). Not in Keel's `users` table.
- Confirm existing & reuse: `tenants`, `users`, `audit_log`, `outbox`, `usage_event` (from Day-6 §0).

## S1. Auth — admin + operator (one mechanism, two roles)

Reuse the existing fastapi-users + JWT setup. **No new auth library.**

- **Login** — reuse the existing login endpoint (e.g. `POST /auth/login`, email + password). On success, issue a JWT:
  - admin: `{ sub: user_id, role: "tenant_admin", tenant_id, exp }`
  - operator: `{ sub: user_id, role: "platform_operator", exp }`  ← **no `tenant_id`**
  - No sensitive fields in the token (signed, not encrypted). Passwords are argon2/bcrypt-hashed (fastapi-users default).
- **`require_role(role)`** dependency — already used for `tenant_admin`; add `platform_operator`. Reject mismatched role with `403`.
- **Admin routes** keep their existing chain: `require_role("tenant_admin")` + admin RLS dependency (sets `app.tenant_id` from the **admin's own** token).
- **Operator routes** use `require_role("platform_operator")` and a **plain session — never `db_with_tenant`, never the admin RLS dependency.** Operator queries hit platform-domain tables only.
- **`assert_tenant_active(tenant_id, session)`** — raises `403` if `tenants.status != 'active'`. **Applied at the Keel boundary only:** the `/portal/keel-token` mint, `/chat`, and the Keel admin login/token for that tenant. **Not at `/portal/login` or any `/portal/*` SIS read.** Suspension disables *Keel* (the AI layer), not the university's SIS portal — Keel has no authority over the SIS. A suspended tenant's students still log into the portal and see My Schedule; they just can't open/use the widget. Gating both the mint **and** `/chat` closes the ≤15-min window where a token minted just before suspension would still work. The operator is never gated (no tenant scope).

## S2. Platform operator endpoints (NEW) — `platform` module, role `platform_operator`

All require `require_role("platform_operator")`. **None depend on tenant RLS or tenant-content repositories.**

- `GET /platform/tenants` — list: `(id, name, status, created_at)` + lightweight counts (e.g. `#students`, `#admins`) computed from platform-domain joins. **No content.**
- `POST /platform/tenants` — **provision.** Body `{ name, admin_email }`. Creates a `tenants` row (`status='active'`) + a bootstrap `tenant_admin` user for it (temp password or invite flag). Writes `platform_audit('provision')`. Returns `{ tenant_id, admin_email }`. **Creates no catalog/students and no `sis_integration`.**
- `POST /platform/tenants/{id}/suspend` — set `status='suspended'`; `platform_audit('suspend')`.
- `POST /platform/tenants/{id}/unsuspend` — set `status='active'`; `platform_audit('unsuspend')`.
- `POST /platform/tenants/{id}/erase` — **confirmation-gated.** Body `{ confirm_name }` must equal the tenant's name. Enqueue `erase_tenant(id)` RQ job; `platform_audit('erase', detail={requested:true})`. Returns `{ status: "queued" }`. Idempotent.
- `GET /platform/cost?period=week|month|day` — calls `platform_usage_summary`; returns per-tenant aggregate rows (S4).
- `GET /platform/audit?limit=` — read-only `platform_audit` (the operator's own action log).

**There is deliberately no endpoint that returns conversations, plans, transcripts, RAG chunks, or any tenant-content row.** That absence is the guarantee.

## S3. Erase worker (RQ) — `erase_tenant(tenant_id)`

- Cascade-delete every row carrying `tenant_id`, batched/transactional: Keel-domain (plans, conversations, `usage_event`, `notification`, tenant-scoped `audit_log`, tenant `outbox`, pgvector chunks) **and** demo SIS-domain seed (`enrollments`, `requests`, `sections`, `courses`, students/transcripts for that tenant).
- Delete the tenant's `tenant_admin` users; delete MinIO blobs prefixed by `tenant_id`; delete the `tenants` row last.
- Write `platform_audit('erase', detail={counts:{...}})`. Return counts, **never content**.
- **Idempotent:** if the tenant is already gone, no-op success.

## S4. Operator cost — aggregate-only doorway

```sql
CREATE OR REPLACE FUNCTION platform_usage_summary(p_period text)
RETURNS TABLE(tenant_id uuid, kind text, calls bigint, tokens bigint, cost numeric)
LANGUAGE sql SECURITY DEFINER AS $$
  SELECT tenant_id, kind, count(*), coalesce(sum(tokens),0), coalesce(sum(cost_estimate),0)
  FROM usage_event
  WHERE created_at >= now() - (CASE p_period
        WHEN 'month' THEN interval '30 days'
        WHEN 'day'   THEN interval '1 day'
        ELSE interval '7 days' END)
  GROUP BY tenant_id, kind;
$$;
```

- `SECURITY DEFINER` lets it read across tenants; it **can only return aggregate columns** — no content exists to leak. The function owner must be allowed to bypass `usage_event` RLS (table owner, or a role with `BYPASSRLS`). If `FORCE ROW LEVEL SECURITY` is on, define the function under a `BYPASSRLS` role.
- **Implemented (D-R-008): the `BYPASSRLS` role is `keel_definer`** (`NOLOGIN BYPASSRLS`, created by the superuser in `scripts/db-init.sh`, granted to `keel_app` as membership). It **owns every *genuinely* cross-tenant `SECURITY DEFINER` function**, not just this one — the **Keel-console login lookup** (`keel_find_user_by_email`: one host serves operator + both tenants' admins, so the tenant is unknown until the user is found), the other operator aggregates (`platform_count_admins`/`platform_count_students`), and the startup bootstrap reads (`tenant_names_all`, `widget_config_all`, `widget_origins_all`). These must read **before** any `app.tenant_id` session exists. `keel_app` stays `NOBYPASSRLS`; only these vetted, read-only functions bypass — via a no-login role, never the `postgres` superuser. Migration 0011 reassigns ownership. *(A prior change owned them by `keel_app`, so they ran under RLS with no tenant and returned only `tenant_id IS NULL` rows — only the operator could log in. See DECISIONS D-R-008.)*
- **Portal lookups are NOT in this set (D-R-015).** The portal server knows its own tenant (`PORTAL_TENANT`) and resolves it via the no-RLS `tenants` table, so `portal_find_by_email` / `portal_find_student` / `portal_list_students` were replaced with ordinary **RLS-scoped** queries (`withTenantTx`) and **dropped** by migration 0012. Portal login now enforces the cross-portal match *structurally* (a foreign email → 0 rows → generic 401), not via an app-side `tenant_id` comparison.
- The operator endpoint calls **only this function** — never a raw `usage_event` select.
- This is the **same `usage_event` source** the admin cost view uses; admin sees their own tenant (RLS-scoped), operator sees all tenants (aggregate-only). One table, two scopes.

## S5. Operator UI (React) — follows `frontend.md`

Build a **Platform Console** surface using the **same stack, app shell, API client, token handling, and styling the admin console uses in `frontend.md`** — do not invent new conventions. Token stored in memory (match admin), role-gated to `platform_operator`; a `tenant_admin` token is rejected.

Pages:
- **Login** — email + password → operator JWT.
- **Tenants** — table `(name, status, created_at, counts)` with row actions: **Suspend/Unsuspend**; **Erase** (modal requiring the operator to type the tenant name); **Provision** (form: name + admin email).
- **Cost** — per-tenant aggregate table `(tenant, kind, calls, tokens, cost)` + period selector. Label it explicitly: *"usage metadata — no conversation content."*
- **Audit** — `platform_audit` action log.

**The Platform Console has no view of any tenant's conversations, plans, or data — by design.** State this in the UI copy near the cost page.

## S6. Seeding (`seed.py` — idempotent)

- Upsert **1 platform operator**: e.g. `operator@keel.platform`, role `platform_operator`, `tenant_id = NULL`, password from `Settings.keel_operator_password` (env, with a documented demo default).
- Upsert **1 `tenant_admin` per demo tenant**: e.g. `admin@tenant-a.edu`, `admin@tenant-b.edu`, role `tenant_admin`, `tenant_id` set.
- **Idempotent by email** (get-or-create / `ON CONFLICT DO NOTHING`). Rerun is complete and in sync.
- Record demo credentials in `RUNBOOK.md`; note plainly they are demo-only, never production.

## S7. Acceptance criteria (testable)

- [ ] Admin and operator both log in with email + password; tokens carry the right role; operator token has **no `tenant_id`**.
- [ ] DB rejects creating a `platform_operator` with a `tenant_id`, and a `tenant_admin` without one (check constraints).
- [ ] Operator token on any tenant-content route (chat, `/portal/*`, plans) → rejected; operator **cannot** mint a widget token.
- [ ] Provision creates a tenant + bootstrap admin (no catalog, no `sis_integration`); `platform_audit` row written.
- [ ] Suspend → that tenant's `/portal/keel-token` mint, `/chat`, and Keel admin login all `403`; **`/portal/login` and `/portal/schedule` still work (200)** — Keel is dark, the SIS portal is up. Unsuspend restores.
- [ ] Erase (correct `confirm_name`) cascades by `tenant_id`, returns counts, is idempotent on re-run; `platform_audit('erase')` survives.
- [ ] `GET /platform/cost` returns per-tenant aggregates for the operator; the same call is `403` for a `tenant_admin`.
- [ ] `seed.py` rerun leaves exactly one operator + one admin per tenant (no duplicates).

## S8. Portal student authentication (portal-domain — NOT Keel)

Replaces the `student_id` switcher with real email + password. **All of this lives in the mock portal / SIS domain. Keel is untouched.**

- **`portal_user`** (NEW, SIS/portal-domain, RLS-scoped by `tenant_id`): `(id, tenant_id, role ('student'|'registrar'), email unique, hashed_password, student_id nullable FK→ SIS student row, created_at)`. For students, `student_id` links the SIS record; for registrar, `student_id` null.
- **`POST /portal/login`** — **body now `{ email, password }`** (was `{ student_id }`). Flow:
  1. Look up `portal_user` by **unique email** (pre-tenant bootstrap query — same pattern the Keel admin login already uses).
  2. Verify password (argon2/bcrypt). Generic `401` on any failure (no user-enumeration).
  3. **Assert the record's `tenant_id` == this portal instance's configured tenant** (`PORTAL_TENANT`); else `403`. (Northane student can't log into Summit portal.)
  4. Set the signed http-only session cookie `{ student_id, tenant_id, role }`. Return `200`.
  - **No suspend check here.** Portal login is a SIS surface and stays up even when the Keel tenant is suspended.
- **`POST /portal/logout`** — clears the cookie (unchanged).
- **`GET /portal/keel-token`** — **unchanged contract**, plus the suspend check: reads `{student_id, tenant_id}` from the **session** (never body/query), runs the origin check, **calls `assert_tenant_active(tenant_id)` → `403` if suspended**, then mints the Keel JWT. The widget never sends `student_id`. When suspended, the widget gets `403` and shows an "advising assistant unavailable for your institution" state — the rest of the portal works normally.
- **Registrar login** uses the same endpoint; role comes from `portal_user.role`. Session role gates `/portal/*` (student) vs `/portal/registrar/*` (registrar).
- **Passwords never leave the portal domain.** Keel's `users` table is not touched by portal auth.

## S9. Second tenant portal — Summit (one image, two instances)

- **One portal frontend image**, run as **two compose services**: `portal-northane`, `portal-summit`. Identical code; differ only by env:
  - `PORTAL_TENANT` (tenant id/slug the instance is bound to), `PORT`/published origin (e.g. Northane `:3001`, Summit `:3002`), `VITE_WIDGET_ID` + widget snippet for that tenant, `VITE_BRANDING` (name/colors).
- **Backend `PORTAL_TENANT` binding:** the portal backend resolves its instance tenant from env and enforces it in `/portal/login` (S8 step 3) and when reading SIS-domain data (session tenant + RLS).
- **Per-tenant `allowed_origins`:** each tenant's Keel widget-config must include its portal origin (Northane origin in Northane's `allowed_origins`, Summit origin in Summit's). This is what the Keel origin-check validates at token mint. Seed/config both (S10).
- **Widget embed:** each instance embeds the widget with its own tenant's snippet (`data-widget-id`), so the launcher on Summit's page mints Summit tokens only.
- **Follows `frontend.md`** exactly — it *is* the Northane portal code, parameterized. No forked components.

> **Demo vs prod:** in production these are two different universities' SIS portals. In the demo it is one mock-portal image rendered per tenant over the shared mock SIS (one Postgres, two logical domains, RLS) — honest because the mock SIS is shared infrastructure, while the two origins still prove the boundary.

## S10. Portal seeding (both tenants) — `seed.py`, idempotent

- **3 students per tenant** (6 total), realistic emails, each a `portal_user(role='student')` linked via `student_id` to a coherent SIS record (transcript + a few enrollments so My Schedule renders):
  - Northane: `alisar@northane.edu`, `omar@northane.edu`, `lina@northane.edu`
  - Summit: `maya@summit.edu`, `jad@summit.edu`, `sara@summit.edu`
- **1 registrar per tenant**: `registrar@northane.edu`, `registrar@summit.edu` (`portal_user(role='registrar')`).
- Shared documented **demo password** (from `Settings`, env-overridable); record in `RUNBOOK.md`, demo-only.
- **Reconcile existing student data:** ensure every seeded student has the right `tenant_id` and links; remove the old switcher path. Idempotent by email; rerun-safe.
- Ensure each tenant's Keel widget-config `allowed_origins` includes that tenant's portal origin.

## S11. Acceptance criteria — portal + cross-tenant (testable)

- [ ] `POST /portal/login {email,password}` sets a session scoped to that student+tenant; wrong password → `401`; body/query `student_id` is ignored.
- [ ] A Northane student logging into the **Summit** portal instance → `403` (tenant mismatch).
- [ ] Northane student → Northane portal → widget token → reads **only** Northane data; a Northane widget token cannot read Summit rows (RLS proven on a pooled connection, with real two-tenant data).
- [ ] Token-mint request carrying the **wrong tenant's origin** → `403` (origin check, now demonstrable across two origins).
- [ ] Suspend Summit (operator) → Summit students **still log into the Summit portal and see My Schedule**, but `/portal/keel-token` and `/chat` return `403` and the widget shows "unavailable"; Northane fully unaffected. Unsuspend restores the widget.
- [ ] `seed.py` rerun leaves exactly 3 students + 1 registrar per tenant, correctly linked, no duplicates.

---

# PLAN section  (→ append to `plan.md`)

## P1. Build order
1. **Migrations** — `tenants.status`, nullable `users.tenant_id`, role value, check constraints, `platform_audit`, `platform_usage_summary`.
2. **Auth** — issue role-stamped tokens; `require_role("platform_operator")`; `assert_tenant_active`. Wire the suspend gate into portal/admin login the same day.
3. **Operator endpoints** — tenants list / provision / suspend / unsuspend / erase(enqueue) / cost / audit.
4. **Erase worker** — `erase_tenant` RQ job.
5. **Seed** — idempotent operator + admins.
6. **Platform Console UI** — per `frontend.md`.
7. **CI gates** — same day each piece lands.

## P2. Token issue (reuse fastapi-users)
```python
def issue_token(user) -> str:
    claims = {"sub": str(user.id), "role": user.role,
              "iat": now(), "exp": now() + ACCESS_TTL}
    if user.role == "tenant_admin":
        claims["tenant_id"] = str(user.tenant_id)   # operator: omitted, by design
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
```

## P3. Suspend gate (dependency)
```python
async def assert_tenant_active(tenant_id, session):
    status = await session.scalar(
        text("SELECT status FROM tenants WHERE id = :t"), {"t": tenant_id})
    if status != "active":
        raise HTTPException(403, "tenant suspended")
```
Call inside `/portal/keel-token` (before mint), `/chat`, and Keel admin login. **Not** inside `/portal/login` or `/portal/*` SIS reads — suspension darkens Keel, not the SIS portal.

## P4. Operator router (no tenant scope)
```python
router = APIRouter(prefix="/platform",
                   dependencies=[Depends(require_role("platform_operator"))])
# uses get_session directly — NEVER db_with_tenant, NEVER tenant-content repos
```
Rule: nothing under `/platform` imports a tenant-content repository. Grep-enforce in CI (P8).

## P5. Provision (Keel shell only)
```
async with session.begin():
    tenant = Tenant(name=body.name, status="active")
    session.add(tenant); await session.flush()
    admin = User(email=body.admin_email, role="tenant_admin",
                 tenant_id=tenant.id, hashed_password=temp_pw())
    session.add(admin)
    session.add(PlatformAudit(actor_user_id=op.id, action="provision",
                              target_tenant_id=tenant.id,
                              detail={"admin_email": body.admin_email}))
# no catalog/students, no sis_integration
```

## P6. Erase (enqueue → worker)
- Endpoint validates `confirm_name == tenant.name`, enqueues, audits `requested`, returns `queued`.
- Worker `erase_tenant`: delete-by-`tenant_id` across all tenant-bearing tables (Keel + demo SIS) → delete tenant admins → delete MinIO prefix → delete tenant row → audit counts. Idempotent (missing tenant = no-op).
- `platform_audit` is **not** tenant-scoped, so it persists after erase.

## P7. Cost
- Endpoint calls `platform_usage_summary(period)` only. Returns grouped rows. Same `usage_event` source as the admin view; operator scope = all tenants, aggregate-only.

## P8. Risks
- **Operator leaking into tenant scope** — the whole grade. Mitigate structurally (no `tenant_id`, no content repo under `/platform`) and prove with the CI gate, not a code review.
- **Cost RLS bypass** — keep the bypass confined to the one `SECURITY DEFINER` aggregate function; never grant the operator a raw `usage_event` select.
- **Erase is destructive** — confirmation + async + idempotent + audit. Suspend is the reversible default; reach for erase deliberately.

## P9. Cut order (if behind)
1. Cost page UI (keep `platform_usage_summary` + endpoint).
2. Audit page UI (keep `platform_audit` writes).
3. Provision UI (keep endpoint).
**Never cut:** the operator auth boundary · `assert_tenant_active` suspend gate · the CI isolation gate · `platform_audit` on every action.

## P10. Portal auth (portal-domain)
```python
@router.post("/portal/login")
async def portal_login(body: LoginIn, request: Request, session=Depends(get_session)):
    # bootstrap lookup by unique email — pre-tenant, same shape as Keel admin login
    user = await session.scalar(
        select(PortalUser).where(PortalUser.email == body.email))
    if not user or not verify_pw(body.password, user.hashed_password):
        raise HTTPException(401, "invalid credentials")          # generic, no enumeration
    if str(user.tenant_id) != settings.portal_tenant:            # instance is tenant-bound
        raise HTTPException(403, "wrong portal for this account")
    # NO suspend check here — portal login is a SIS surface, stays up when Keel is suspended
    set_portal_cookie(response, {"student_id": user.student_id,
                                 "tenant_id": user.tenant_id, "role": user.role})
    return {"ok": True}
```
- `/portal/keel-token` is **unchanged except for the suspend gate** — reads the session, origin-checks, calls `assert_tenant_active` (`403` if suspended), then mints. This is where Keel goes dark, not at login.
- Registrar login is the same call; `role` drives `/portal/registrar/*` gating.

## P11. Second portal (one image, two services)
```yaml
# docker-compose.yml — same image, two instances
portal-northane:
  image: keel-portal
  environment: { PORTAL_TENANT: <northane-id>, VITE_WIDGET_ID: <northane-widget>, PORT: 3001 }
  ports: ["3001:3001"]
portal-summit:
  image: keel-portal
  environment: { PORTAL_TENANT: <summit-id>, VITE_WIDGET_ID: <summit-widget>, PORT: 3002 }
  ports: ["3002:3002"]
```
- Each tenant's Keel widget-config `allowed_origins` must list its instance origin (`http://localhost:3001` / `:3002`). The origin check reads these.
- Backend uses `settings.portal_tenant` to bind login + scope SIS reads.

## P12. Cross-tenant demo flow (what to show)
1. Log into Northane portal as `alisar@northane.edu` → My Schedule (Northane data) → open widget → plan → approve → enroll → `via Keel` badge.
2. Log into Summit portal as `maya@summit.edu` (different origin) → only Summit data.
3. Prove isolation: a Northane widget token replayed against Summit → rejected; Summit-origin mint for Northane tenant → `403`.
4. Operator suspends Summit → Summit students still log in and see My Schedule, but the widget goes dark (token mint + chat `403`); Northane keeps working. Unsuspend → widget back.

---

# TASKS section  (→ append to `tasks.md`)

Tags: **[CRIT]** critical path · **[CI]** gate · **[REAL]** functional · **[THIN]** cuttable · **[SKIP]** don't build.

## PO-0. Migrations
- [ ] **[CRIT]** Alembic: `tenants.status` enum default `'active'`.
- [ ] **[CRIT]** Alembic: `users.tenant_id` nullable; add `'platform_operator'` role value (autocommit if native enum).
- [ ] **[CRIT]** Alembic: check constraints `ck_operator_no_tenant`, `ck_admin_has_tenant`.
- [ ] **[CRIT]** Alembic: `platform_audit` table (NOT RLS-scoped).
- [x] **[CRIT]** Alembic: `platform_usage_summary(text)` SECURITY DEFINER fn + `GRANT EXECUTE`.
- [x] **[CRIT]** `keel_definer` (`NOLOGIN BYPASSRLS`) role bootstrapped in `scripts/db-init.sh`, granted to `keel_app`; migration 0011 reassigns the genuinely cross-tenant SECURITY DEFINER functions (Keel login lookup + operator aggregates + bootstrap reads) to it so they read pre-session. `keel_app` stays `NOBYPASSRLS`. (D-R-008.)
- [x] **[SEC]** Portal lookups RLS-scoped, not BYPASSRLS: portal server resolves `PORTAL_TENANT` → tenant_id and queries under `withTenantTx`; migration 0012 drops `portal_find_by_email`/`portal_find_student`/`portal_list_students`. Cross-portal match is now structural. (D-R-015.)

## PO-A. Auth (admin + operator)
- [ ] **[CRIT]** Role-stamped token issue (operator token has no `tenant_id`).
- [ ] **[CRIT]** `require_role("platform_operator")`.
- [ ] **[CRIT]** `assert_tenant_active` wired into `/portal/login`, `/portal/keel-token`, admin login.
- [ ] **[CI]** Constraint test: operator-with-tenant and admin-without-tenant both rejected by DB.

## PO-B. Operator endpoints
- [ ] **[CRIT][REAL]** `GET /platform/tenants` (platform-domain reads + counts; no content).
- [ ] **[CRIT][REAL]** `POST /platform/tenants` provision (tenant shell + bootstrap admin + audit).
- [ ] **[CRIT][REAL]** `POST /platform/tenants/{id}/suspend` · `/unsuspend` (+ audit).
- [ ] **[CRIT][REAL]** `POST /platform/tenants/{id}/erase` (confirm_name → enqueue + audit).
- [ ] **[REAL]** `GET /platform/cost` (calls aggregate fn only).
- [ ] **[REAL]** `GET /platform/audit` (read `platform_audit`).

## PO-C. Erase worker
- [ ] **[CRIT][REAL]** `erase_tenant` RQ job: cascade delete-by-`tenant_id` (Keel + demo SIS) → admins → MinIO prefix → tenant row → audit counts. Idempotent.

## PO-D. Platform Console UI (follow `frontend.md`)
- [ ] **[CRIT]** Login (operator JWT, in-memory, role-gated).
- [ ] **[CRIT]** Tenants page: list + suspend/unsuspend + provision form + erase modal (type-name confirm).
- [ ] **[REAL]** Cost page: per-tenant aggregate table + period selector + "no content" label.
- [ ] **[THIN]** Audit page.

## PO-E. Seeding
- [ ] **[CRIT]** `seed.py`: upsert 1 operator + 1 admin per demo tenant; idempotent by email; rerun-safe.

## PO-F. CI gates
- [ ] **[CI]** Operator token → every tenant-content route (chat, `/portal/*`, plans) rejected; operator cannot mint a widget token.
- [ ] **[CI]** Grep: nothing under `/platform` imports a tenant-content repository.
- [ ] **[CI]** Suspended tenant → `/portal/keel-token` mint, `/chat`, and Keel admin login `403`; **`/portal/login` + `/portal/schedule` stay `200`**; unsuspend restores.
- [ ] **[CI]** Erase idempotent; `platform_audit('erase')` survives the erase.
- [ ] **[CI]** `/platform/cost` returns aggregates for operator, `403` for `tenant_admin`.

## PT-0. Migrations (portal)
- [ ] **[CRIT]** Alembic: `portal_user` table (SIS/portal-domain, RLS by `tenant_id`, unique email, FK→student).

## PT-A. Portal student auth (portal-domain) — see plan §P10
- [ ] **[CRIT][REAL]** `POST /portal/login` → `{email,password}`; bootstrap email lookup; verify hash; tenant-match vs `PORTAL_TENANT`; `assert_tenant_active`; set session cookie. Generic `401`.
- [ ] **[CRIT]** Remove the old `student_id` switcher path; keep `/portal/keel-token` mint **unchanged**.
- [ ] **[REAL]** Registrar login via same endpoint; session role gates `/portal/registrar/*`.
- [ ] **[CI]** Wrong password → `401`; body `student_id` ignored; cross-portal login (Northane acct on Summit) → `403`.

## PT-B. Second tenant portal (Summit) — one image, two services — see plan §P11
- [ ] **[CRIT]** Parameterize portal frontend by env (`PORTAL_TENANT`, `VITE_WIDGET_ID`, branding, port). No forked code; follows `frontend.md`.
- [ ] **[CRIT]** Backend honors `settings.portal_tenant` for login binding + SIS read scope.
- [ ] **[CRIT]** Compose: `portal-northane` + `portal-summit` from one image, two origins.
- [ ] **[CRIT]** Each tenant's Keel widget-config `allowed_origins` includes its portal origin.

## PT-C. Portal seeding — see spec §S10
- [ ] **[CRIT]** `seed.py`: 3 students + 1 registrar per tenant, realistic emails, linked SIS data; idempotent by email.
- [ ] **[CRIT]** Reconcile existing student seed → correct `tenant_id` + links for both tenants; rerun-safe.

## PT-D. CI gates (cross-tenant)
- [ ] **[CI]** Northane widget token cannot read Summit rows (RLS on a pooled connection, real two-tenant data).
- [ ] **[CI]** Wrong-tenant origin at token mint → `403`.
- [ ] **[CI]** Suspend Summit → Summit `/portal/keel-token` + `/chat` `403` while `/portal/login` + `/portal/schedule` stay `200`; Northane unaffected.

### Cut order if behind
1. Cost page UI → 2. Audit page UI → 3. Provision UI → 4. Summit branding polish (keep the second origin + login).
**Never cut:** operator auth boundary · suspend gate · the CI isolation gate · `platform_audit` on every action · **portal email/password auth · the second origin · cross-tenant CI gate.**