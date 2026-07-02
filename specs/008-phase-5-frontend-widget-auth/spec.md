# Keel — Day 6 SPEC (Implementation Contracts)

**Goal:** three surfaces working, the widget embeddable + safely authenticated, background intelligence running. This is the contract Claude Code implements against. Read `docs/PRODUCTION.md` for architecture.

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

---

## 9. Frontend design & UX (three surfaces, two skins)

> Merged from the former `frontend.md`. The visual/UX contract for the three React
> surfaces. Where this and §1–§8 disagree on a route/payload, §1–§8 win; on look/UX, this
> wins. Rationale for the service/auth split is in DECISIONS D-P5-001…006.

### 9.1 The rule that shapes the frontend

Keel is an AI layer **on top of** the university's SIS — the UI must *show* that boundary.
The three surfaces deliberately do not look the same:

| Surface | Whose product | Skin |
|---|---|---|
| **Mock SIS portal** (host page · My Schedule · registrar) | the university's SIS | **Institutional** — light, plain, squarer, "official records system" |
| **Student widget** (chat · plans · approval) | **Keel** | **Keel-dark** — navy, calm, premium, focused |
| **Keel console** (registrar admin · platform operator) | **Keel** | **Keel-light** dashboard |

The dark widget popping against the plain portal is the demo's strongest visual proof of the
boundary. The **approval moment** is the second bold thing; everything else stays quiet.

### 9.2 Design tokens

**Color (base palette):** `--moonlight #F0ECDD` (light text/bg, primary fill on dark),
`--frost #8BA3C5` (muted accent/borders), `--steel #495B7D` (secondary), `--storm #23354D`
(dark panels / light headers), `--oxford #02122F` (widget bg / light-surface text).
**Status/action extensions:** `--accent #5BC2E7` (focus/active/streaming — the one bright
color, used sparingly), `--risk-ontrack #3E8E7E`, `--risk-atrisk #D9A441` (calm amber, not
alarmist red), workload light/medium/heavy = frost/steel/amber. Primary action: cream on navy
in the widget, `--storm` fill on light surfaces; one primary per screen.

**Type:** display/headings = a calm serif (Fraunces / Source Serif 4), used sparingly for
"institutional/trustworthy"; body/UI/data = Inter (or IBM Plex Sans); optional IBM Plex Mono
for IDs/audit/cost. Scale 2.0/1.5/1.25/1.0/0.875/0.75 rem, body line-height 1.5.

**Spacing/shape/motion:** 4px base (4/8/12/16/24/32/48); radius 10–12px on Keel cards, 6px on
the squarer portal; one soft shadow for the widget/floating cards, flat portal; minimal
motion (tokens stream in, ~150ms launcher open), respect `prefers-reduced-motion`. No ambient
particles or glow. Avoid generic "AI" stock art (glowing caps, circuits, neon lightbulbs).

### 9.3 Shared primitives (`frontend/ui/`, built once, two skins)

`Button` (primary/secondary/ghost/danger, loading/disabled) · `Badge` (`via-keel`,
`risk-*`, `load-*`, `status-*`) · `Card`/`Panel` · `Field` · `Table` · `Tabs` · `Toast` ·
`EmptyState` · `Spinner` · `Modal` (approval confirm) · `StreamingText` (SSE). Label controls
by what the student/registrar controls, never by system internals; button verbs state the
result ("Approve & enroll" → toast "Enrolled"); errors say what to fix, never apologize.

### 9.4 Surface specifics

- **Widget** (Keel-dark, ~380px column / full-screen sheet on mobile): header (serif wordmark
  + persona + close), streaming message column, the **PlanCard hero** (name, term, credits,
  course rows, **always a risk badge + a workload badge rendered as facts**, LLM "why" text,
  compare strip for 2–3 candidates, Save/Activate/**Approve & enroll**), composer disabled
  while a write awaits approval.
- **Keel console** (Keel-light, config only — **no** catalog/student CRUD, no rule editing, no
  request queue): RAG upload · widget config (persona/origins/tools; safety rails locked and
  shown as enforced-in-code) · read-only snippet · cost table · audit table. The platform
  operator's pages (Tenants provision/suspend/erase · aggregate Cost · Audit) share this shell.
- **Mock SIS portal** (institutional, invented fictional university name — never a real one):
  student switcher/login · **My Schedule (REAL — the write-proof, "via Keel" badge)** ·
  Requests · Activity · section-search + submit-petition **stage-set** (render, no endpoints) ·
  registrar **Request Queue (REAL)** + read-only Catalog/Sections/Students/Rules with dead buttons.

### 9.5 Critical UX rules

1. The **approval gate is sacred** — no write without an explicit tap on a clearly-labeled
   button behind a confirm modal that restates exactly what will happen. Until approval, a plan
   is a *proposal*.
2. **Never show a write as done before it is** — the "via Keel" row appears only after the real
   write returns.
3. Risk/workload are **predictions** — label them so; the at-risk badge offers mitigation, never
   scolds.
4. Streaming/loading + empty/error states on every async call; token lives **in memory only**,
   `student_id` is never sent from the client.

### 9.6 Assets

Logos live in `frontend/static/` and `frontend/portal/public/` (`keel-logo`, `keel-icon`,
`creamy-keel-icon`, `final-keel-logo`). The portal uses an invented fictional university
identity (no real university's name or logo).

---

## 10. Phase 5 Addendum — platform operator, admin/operator auth, second tenant portal

> Merged from the former `missing_recovery.md`. Full rationale: DECISIONS D-A-001…005 (and
> the auth/RLS remediation D-R-008, D-R-011, D-R-015). This section is the contract; the
> decisions carry the "why".

### 10.1 Data model

- `tenants.status` — `('active'|'suspended')`, default `'active'` (platform-domain).
- `users.tenant_id` — **nullable** (operators have none); `users.role` includes
  `'platform_operator'`. Two DB check constraints make isolation a database fact:
  `ck_operator_no_tenant` (operator ⇒ `tenant_id IS NULL`) and `ck_admin_has_tenant`
  (admin ⇒ `tenant_id IS NOT NULL`).
- `platform_audit(id, actor_user_id, action, target_tenant_id nullable, detail jsonb,
  created_at)` — **not RLS-scoped**; `ON DELETE SET NULL` on the FK so it survives tenant erase.
- `platform_usage_summary(period)` — `SECURITY DEFINER` aggregate function returning grouped
  numbers only (owned by the `keel_definer` `BYPASSRLS` role, D-R-008).
- `portal_user(id, tenant_id, role ('student'|'registrar'), email unique, hashed_password,
  student_id nullable FK, created_at)` — **portal/SIS-domain**, RLS-scoped; holds portal login
  credentials, never in Keel's `users` table.

### 10.2 Auth — one mechanism, two Keel roles

- `POST /auth/login` (email + bcrypt) issues a role-stamped JWT: admin `{sub, role:tenant_admin,
  tenant_id, iat, exp}`; operator `{sub, role:platform_operator, iat, exp}` — **no `tenant_id`**.
- `require_role(role)` guards routes; operator routes use a **plain session — never
  `db_with_tenant`** and touch platform-domain tables + aggregate functions only.
- `assert_tenant_active(tenant_id)` — raises 403 if suspended. Applied at the **Keel boundary
  only**: `/internal/mint-token`, `/chat`, Keel admin login. **Not** at `/portal/login` or
  `/portal/*` SIS reads — suspension darkens Keel, not the university's SIS.

### 10.3 Operator endpoints (`/platform/*`, role `platform_operator`)

`GET /platform/tenants` (list + counts, no content) · `POST /platform/tenants` (provision =
tenant shell + bootstrap admin + audit; **no** catalog/students, **no** `sis_integration`) ·
`.../suspend` · `.../unsuspend` · `.../erase` (confirmation-gated by `confirm_name`, enqueues
an async idempotent cascade-delete worker; `platform_audit` survives) · `GET /platform/cost`
(aggregate function only) · `GET /platform/audit`. **There is deliberately no endpoint that
returns any tenant-content row** — that absence is the isolation guarantee.

### 10.4 Portal student auth (portal-domain — not Keel) + second tenant

- `POST /portal/login {email, password}` — bcrypt verify; the record's `tenant_id` must equal
  the instance's `PORTAL_TENANT` (a Northane account cannot log into the Summit portal → 403);
  generic 401 on failure (no enumeration); sets a signed http-only session cookie. No suspend
  check here. `/portal/keel-token` is unchanged except it now calls `assert_tenant_active`.
- **Second portal = one image, two compose services** (`portal-northane` :3001,
  `portal-summit` :3002), differing only by env (`PORTAL_TENANT`, port, widget id, branding).
  Two origins make the Keel origin-check demonstrable across tenants; each tenant's
  `widget_config.allowed_origins` lists its own portal origin.
- Portal reads run **RLS-scoped** under `withTenantTx` (the portal knows its own tenant), not
  via `BYPASSRLS` functions (D-R-015).

### 10.5 Addendum acceptance

- [ ] Operator token has no `tenant_id`; DB rejects operator-with-tenant and admin-without-tenant.
- [ ] Operator token on any tenant-content route → rejected; operator cannot mint a widget token.
- [ ] Suspend a tenant → its `/internal/mint-token`, `/chat`, Keel admin login all 403, but
  `/portal/login` + `/portal/schedule` stay 200; unsuspend restores.
- [ ] Erase (correct `confirm_name`) cascades by `tenant_id`, is idempotent; `platform_audit('erase')` survives.
- [ ] `GET /platform/cost` → aggregates for the operator, 403 for a `tenant_admin`.
- [ ] A Northane account on the Summit portal → 403; a Northane widget token cannot read Summit rows.

---

## 11. Section-selection registration flow (planning → registration)

> Merged from the former `registration-section-flow.md`. Full rationale: DECISIONS
> D-P6-001/002. Saved/named plans (A4) are deferred to STRETCH — registration operates on
> the plan selected **in the current conversation**, not a persisted one.

**Principle applied to sections:** intelligence proposes, the engine verifies. Section choice
is **agentic, not portal-style filtering** — the student states preferences in natural
language ("no 8am, no Fridays"), the engine returns the open-section pool, and the LLM reasons
over it to pick a fitting, conflict-free, open section per course; the engine re-verifies every
choice before staging.

- **`propose_sections`** (read-only; realises SPEC §7's `search_sections`) returns each open
  section per course with `section_id`, day/time, instructor, seats, and whether it meets the
  prefs. Distinguishes **full** from **not-offered-this-term** so the agent gives the right
  remedy (waitlist vs. another term). Emits structured section cards via the plan channel.
- **`stage_enrollment`** re-verifies chosen sections (exist, belong to a requested course,
  open, conflict-free) → `ToolError` on any violation → conversational repair. A "you pick for
  me" fallback (`_resolve_sections_for_courses`) greedily picks pref-meeting sections. No tool
  has an `approved` field; `execute_node` re-validates at write.
- **`propose_plan`** flags courses with **no open section** in the target term (keep + flag, never
  silently drop) so a plan is honest about registrability.
- **Re-registering a term replaces the prior registration** in the same transaction, gated on a
  successful new enrollment (D-P6-001). A blunt override for the demo; the production add/drop
  concerns (calendar window, explicit diff confirmation, W-grade rules, aid thresholds,
  waitlist side-effects) are recorded as deferred in D-P6-001.
- **Seed** (migration `0013_section_instructor`): two sections per course per offered term with
  synthetic instructors and varied times (some 8am/Friday/full) so preferences discriminate and
  the full→waitlist branch is exercised.

**Safety unchanged:** `propose_sections` read-only; `stage_enrollment` only stages;
`execute_node` writes only on approved resume. Covered by `tests/unit/test_section_selection.py`
plus the standing write-safety and agent-node suites.