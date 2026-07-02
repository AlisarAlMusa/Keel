# DESIGN.md — Keel System Design (as built)

How Keel is actually constructed today, after the post-integration remediation
pass. `ARCHITECTURE.md` is the conceptual shape; `SPEC.md` the component contracts;
`PRODUCTION.md` the SIS boundary; this document is the **as-built design of record**
— how the pieces fit, where the boundaries are, and why. Where this contradicts
older prose, this file and the code win.

---

## 1. Topology

```
 Browser (student on the SIS portal page)
   │  loads widget.js (served by keel-api) → opens iframe at keel-api/widget/
   │
 Mock SIS portal  (Node/Express + React SPA, one image, two instances)
   ├─ portal-northane :3001   ├─ portal-summit :3002
   │   session cookie (HttpOnly) · reads SIS-domain tables as keel_app (RLS)
   │   POST /api/portal/keel-token ──service secret──► keel-api /internal/mint-token
   ▼
 keel-api (FastAPI, async, layered, RLS)         model-server (joblib, torch-free)
   ├─ /chat  /actions/*  /admin/*  /platform/*    ├─ /predict/intent
   ├─ /auth/login  /internal/mint-token           └─ /predict/grad-risk
   ├─ serves /widget (iframe) · /keel (console)
   │
   ├── services → repositories → domain/engine        worker (RQ + scheduler thread)
   │      (api never imports downward past services)     outbox publish · capacity sync
   │                                                      · expiry sweep (all per-tenant)
   └── Postgres + pgvector (one DB, RLS) · Redis · Vault · MinIO · MLflow
```

Two logical domains live in one Postgres, isolated by Row-Level Security: the
**SIS-domain** (catalog, sections, students, transcripts, enrollments, requests —
seeded) and the **Keel-domain** (plans, conversations, risk, RAG, config, audit,
outbox, cost, actions). The portal reads the SIS-domain directly as `keel_app`
under RLS; keel-api owns both domains through its repository/service layers.

---

## 2. Layering and the dependency rule

`api → services → repositories → domain`, with `infra` wired by DI. The rule is
enforced in review and by import direction:

- **api/** — routers parse, authorize (auth dependency), delegate, serialize. No
  business logic. (The chat router's cost accounting was moved to
  `services/usage.py` to keep this true.)
- **services/** — orchestration: the router, the action pattern, the agent host,
  advising/guidance use-cases.
- **repositories/** — tenant-scoped DB access for the write/ledger/action surface,
  each bound to one `(session, tenant_id)` and asserting row tenant on read
  (defense-in-depth layer 2). See §5.
- **domain/** — pure types + the deterministic **engine** (DAG, audit, verifier,
  sections, workload, planner). No I/O, no framework, no LLM. Verified pure.
- **infra/** — DB, Redis, Vault, MinIO, model-server client, guardrails, tracing,
  RAG, LLM clients. Created once in `lifespan`, exposed via `Depends`.

---

## 3. Request lifecycle (chat)

```
widget POST /chat  (Authorization: Bearer <widget JWT>)
  → get_widget_context: verify JWT (sig, exp, aud, required claims) → tenant_id, student_id
  → verify_origin_or_403: same-origin (the keel-api iframe) or tenant allowlist
  → assert_tenant_active: suspended tenant → 403 (widget goes dark)
  → guardrails.check_input: injection / cross-tenant refusal (hardcoded)
  → set_request_identity(tenant_id, student_id)  [contextvar, from JWT only]
  → router.route():
        intent model → confident → direct flow ; else/ambiguous → bounded agent
  → agent tools run under tenant_session(tenant_id) (RLS) and resolve_identity()
        so the verified identity — never an LLM argument — scopes every query/write
  → guardrails.redact: PII redaction on egress
  → record_chat_usage (best-effort, tenant-scoped)
  → ChatResponse{ response, request_id, router, action_id?, pending_approval? }
```

`action_id` + `pending_approval` are populated when the agent **staged a write**
and the graph paused for approval (§4). The widget uses them to render the
Approve/Decline control.

---

## 4. The action pattern (every write is gated)

One shape, implemented once, reused for enrollment, waitlist, and the four
institutional requests (graduation, major-change, petition, escalation):

```
agent stage_* / institutional tool
  → engine validates preconditions, drafts text
  → ActionsRepository.insert_pending(frozen payload, thread_id)   [status=pending]
  → LangGraph interrupts; run_agent returns action_id + pending_approval
  → widget shows Approve/Decline
  → POST /actions/{id}/approve   (Authorization: Bearer <widget JWT>)   ← THE gate
        identity from the verified JWT (never client headers)
        Layer 1: action loaded under tenant RLS  → cross-tenant = 404
        Layer 2: action.student_id == token.student_id → else 403
        status must be 'pending' → else 409
        thread_id read from the action row (never the request) → resume graph
  → execute_node (approved resume only) re-validates and writes in ONE tx:
        domain row + outbox event   then audit_log row   then action='executed'
  → worker publishes outbox (email/notification) with retry + dedupe
```

Key invariants now enforced:

- **Identity is cryptographic.** `/actions/*` authenticates with the same verified
  widget JWT as `/chat`. The previous `X-Student-Id`/`X-Tenant-Id` header scheme
  (spoofable, and never sent by the widget) is gone.
- **No agent self-approval.** No agent tool carries an `approved` field, and no
  tool calls a write service with `approved=True`. The petition tool that used to
  do so now stages like everything else — closing the injection bypass. Institutional
  filings therefore reach the registrar's queue **only after** the student approves.
- **Execution-time re-validation.** Enrollment re-checks registration holds and
  re-checks section capacity under `SELECT … FOR UPDATE` before inserting, so a
  hold placed (or a seat filled) during the approval window blocks/​skips the write
  and the `enrolled` counter cannot drift or overbook. Waitlist fulfilment does the
  same.
- **Idempotency.** Enrollment uses `(tenant_id, idempotency_key)`; institutional
  filings use the partial-unique PENDING index; re-runs are safe no-ops.

---

## 5. Tenant isolation (the grade) — three independent layers

1. **RLS (database).** Every tenant-owned table is `ENABLE` + `FORCE ROW LEVEL
   SECURITY` with a `tenant_isolation` policy keyed on
   `current_setting('app.tenant_id')`. The app connects as `keel_app`
   (`NOSUPERUSER NOBYPASSRLS`), so an unset tenant matches no rows (fail-closed).
2. **Repository / query scoping.** The write/ledger/action path goes through
   `repositories/` (`LedgerRepository`, `ActionsRepository`, …) bound to a tenant
   and asserting row tenant on read. Read paths filter `WHERE tenant_id = :tid` on
   every query in addition to RLS.
3. **pgvector.** RAG retrieval filters `WHERE tenant_id = :tid` and runs under the
   tenant session.

Agent tools never trust an LLM-supplied `tenant_id`/`student_id`: `run_agent` binds
the JWT identity into a contextvar and the write/stage tools call `resolve_identity`,
which overrides any model-emitted value (a mismatch is logged as tampering). This
closes the "injection scopes a tool to another tenant" vector on the write path; the
read path additionally relies on RLS + the model never possessing another tenant's
UUID.

**Where the worker fits.** `keel_app` is NOBYPASSRLS, so a background scan with no
tenant set returns zero rows. Every worker job therefore enumerates active tenants
from the non-RLS `tenants` table and processes each inside a `tenant_session` — the
fix that brought the outbox/notify, capacity-sync, and expiry tiers back to life.
A scheduler thread in the worker entrypoint enqueues these jobs on an interval.

---

## 6. Authentication map

| Caller | Mechanism | Identity source |
|---|---|---|
| Student widget → `/chat`, `/actions/*` | widget JWT (HS256, ≤15 min, `aud=keel-widget`) | verified token claims only |
| Portal backend → `/internal/mint-token` | `portal_service_secret` (constant-time compare) + server-side `student ∈ tenant` check | portal session (server-side) |
| Registrar/admin → `/admin/*` | admin JWT (`role=tenant_admin`, carries `tenant_id`) | `/auth/login` (bcrypt) |
| Platform operator → `/platform/*` | operator JWT (`role=platform_operator`, **no** tenant) | `/auth/login` (bcrypt) |
| Portal student/registrar → portal pages | portal session cookie (HttpOnly), portal-domain only | `/api/portal/login` (bcrypt) |

The university "authenticates" the student; the portal vouches via the server-to-server
mint. Keel never trusts a client-supplied `student_id`. A suspended tenant darkens
Keel (mint + `/chat` → 403) but not the SIS portal (students still log in and see
their schedule).

---

## 7. Notifications & sync (how the surfaces stay consistent)

- **Single source of truth = the database.** When the widget enrolls a student, the
  `enrollments` row is written under the student's tenant; the portal's read-only
  **My Schedule** (same DB, RLS-scoped to that student) shows it immediately with a
  `via Keel` source badge — isolated per student and per tenant.
- **Institutional requests** (petition/graduation/major-change) write a
  `request_queue` row after approval; the student's portal **Requests** page and the
  registrar's **Request Queue** both read that row. The registrar's approve/reject
  updates the row + writes an `outbox` + `audit_log` entry (the portal's outbox
  insert now includes the NOT-NULL `kind` column).
- **Where a notification goes today.** (1) The **widget** shows an inline
  confirmation at action time. (2) The **outbox → worker** path is the durable
  channel: `outbox_publisher` (per-tenant) enqueues, `send_outbox_event` delivers.
  Email delivery is currently a logged stub (`_send_email`) — wiring real SMTP is a
  one-function change; the outbox guarantee (no dual-write) already holds. The
  registrar→student outcome notification is SIS-domain and is **simulated** via the
  portal's outbox/audit writes; that is intentional and sufficient for the demo.

---

## 8. The deterministic engine & planning loop

Unchanged by the remediation and deliberately so: `domain/engine/` is pure and the
propose→verify→repair loop runs the verifier (`verify`) on every candidate before a
plan is shown or saved (`planning.py`). The risk model (served by model-server) and
the deterministic workload index score only **valid** plans. The greedy planner is
the fallback. The verifier is the seatbelt; the LLM never decides feasibility.

---

## 9. Production seam (unchanged)

The SIS-domain tables are seeded into Keel's Postgres for the demo; the repository
boundary is the seam where a real `SISGateway` + per-tenant adapters land in
production (see `PRODUCTION.md`). The remediation strengthened that boundary by
making the write/ledger/action path actually pass through repositories.

---

## 10. Known limitations / honest gaps (post-remediation)

The four hardening gaps below were **closed** in the second remediation wave
(DECISIONS D-R-010..013); they are kept here for history with their resolution.

- **Read-tool identity override — CLOSED (D-R-010).** All 17 read/advisory tools now
  call `resolve_identity`/`resolve_tenant` at entry, overriding any LLM-supplied
  identity, exactly like the write tools. RLS + per-query filtering remain as
  defense-in-depth beneath that.
- **Per-portal service secrets — CLOSED (D-R-011).** Each portal presents its own
  `portal_service_secret_<slug>`; `mint-token` lets a secret mint only its own
  tenant's tokens (cross-tenant → 403). The shared secret remains a legacy fallback.
- **Structured plan cards — CLOSED (D-R-012).** `propose_plan` surfaces structured
  `PlanData` via `agent/plan_channel.py` → `ChatResponse.plans`; the widget renders
  `PlanCard`/`PlanTabsCard`. The generic Approve/Decline card still backs non-plan
  actions (petition/graduation/escalate).
- **Email transport — CLOSED (D-R-013), delivery still off by default.** `infra/email.py`
  is pluggable (`LoggingEmailSender` default, `SMTPEmailSender` when `keel_smtp_*` is
  set). The demo keeps it logging-only so no real mail is sent. *Residual:* enabling
  SMTP needs the recipient address populated in the outbox payload (currently absent),
  so real delivery is one wire away, not automatic.
- **Approval model vs. SPEC §8 (unchanged by decision).** The implementation uses a
  resumable-graph + a persisted `actions` row (status-gated, single-use by status
  transition) rather than a separate short-lived `ApprovalToken` object. The safety
  property ("no write without explicit, verified, single approval") is equivalent;
  SPEC §8 is annotated to reflect this.

### Auth / RLS note (D-R-008)

Cross-tenant SECURITY DEFINER functions (pre-session login lookup, platform
aggregates, portal/widget bootstrap) are owned by a dedicated `keel_definer` role
(`NOLOGIN BYPASSRLS`, granted to `keel_app` as membership only). `keel_app` itself
stays `NOBYPASSRLS`, so every normal query is RLS-enforced; only these few vetted
functions bypass, and via a no-login role rather than the `postgres` superuser. The
role + ownership + `SELECT` grants are established in `scripts/db-init.sh` (bootstrap)
and migration `0011` (existing DBs).
