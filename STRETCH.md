# Keel — STRETCH (deferred by design)

> Trade-off log for ideas we chose **not** to build now. Each entry states what it
> is, why it's deferred, the trade-off, and what would trigger picking it up. We
> document trade-offs so a deferral is a recorded decision, not an accident.


## Guardrails sidecar (NeMo Guardrails)

**Now:** in-process rails (`infra/guardrails.py`) — injection + cross-tenant refusal
on input, `redact()` on every egress (response, logs, traces). Hardcoded; tenant
config cannot weaken them.

**Deferred:** a separate **NeMo Guardrails sidecar** for topical + injection rails.

**Trade-off:** in-process is one less service, easy to test, and the real tenant
boundary is the DB filter anyway. A sidecar adds config-driven, per-tenant rails and
defense-in-depth, but also a service, extra latency, and ops surface.

**Trigger to build:** when rails must be config-driven per tenant, or the rule set
outgrows in-process patterns.

## Shrink the SECURITY DEFINER / BYPASSRLS surface (tenant-scoped lookups)

**Now:** ten cross-tenant helper functions (`keel_find_user_by_email`, `portal_*`,
`platform_*`, `widget_*`, `tenant_names_all`) run `SECURITY DEFINER` owned by
`keel_definer` (`BYPASSRLS`) because they read *before* a tenant session exists.
`keel_app` stays `NOBYPASSRLS`; only these vetted, read-only functions bypass. See
DECISIONS D-R-008.

**Done (D-R-015):** ~~`portal_find_by_email` / `portal_find_student` /
`portal_list_students`~~ — the portal server now resolves its own tenant
(`PORTAL_TENANT`, via the no-RLS `tenants` table) and runs RLS-scoped queries inside
`withTenantTx`; migration 0012 **dropped** all three functions. Removed from the
bypass set.

**Still deferred:** scope the remaining ones that could avoid bypass:
- **`widget_config_all` / `widget_origins_all` / `tenant_names_all`** — startup
  bootstrap reads; could be a per-tenant loop over the known tenant list (lower
  value: they run once at boot, read-only, no request-time exposure).

**Genuinely must stay cross-tenant:** `keel_find_user_by_email` (the Keel-console
login models the SIS/IdP "find the account" step — tenant is unknown until the user
is found; the console host serves operator + both tenants' admins, so host can't
disambiguate) and `platform_count_*` / `platform_usage_summary` (the platform
operator is cross-tenant **by role**, sees aggregates only, never row data).

**Why not "detect tenant from the portal host"?** The per-tenant *secret* (G2) is a
strictly stronger signal than the Host header (cryptographic vs. spoofable), and we
already have it — so the defended version of "know the tenant first" is *scope by the
authenticated tenant*, not by host.

**Trade-off:** shrinks the bypass surface to only the genuinely-global login +
operator-aggregate functions (defense-in-depth: even portal/widget reads become
RLS-bound), at the cost of refactoring those call sites to set a tenant session and
drop the DEFINER functions. Net security gain is incremental — the current functions
are parameterized, read-only, and fixed — so this is hardening, not a fix.

**Trigger to build:** a security review that wants the BYPASSRLS function count
minimized, or before a real (non-simulated) SIS replaces the portal lookups.

## Real email delivery (SMTP) + per-student addresses

**Now:** email is ON but **simulated** — `LoggingEmailSender` logs the send to a
single demo inbox; only Keel-originated events email (D-R-013).

**Deferred:** flip `KEEL_SMTP_ENABLED=true` + host to use `SMTPEmailSender` (already
implemented), and resolve **real per-student recipient addresses** instead of the
single `keel_email_simulate_to` inbox.

**Trade-off:** real delivery needs SMTP credentials in Vault and a verified sender
domain; the demo deliberately avoids sending real mail.

**Trigger to build:** a deployment with real student mailboxes and an SMTP relay.

## Richer LLM-step tracing (OpenLLMetry / LangSmith)

**Now:** Jaeger + OTel auto-instrumentation (httpx captures each LLM/model-server
call with timing) + manual `agent.turn` / `agent.llm` / `agent.tool.*` spans
(D-R-014). One Jaeger UI, no extra SaaS.

**Deferred:** `traceloop-sdk` (**OpenLLMetry**) to auto-capture prompts/completions/
token-usage per LLM call, and/or **LangSmith** (the `langsmith_tracing` flag is
already wired) for an LLM-native trace UI with eval integration.

**Trade-off:** richer, structured LLM telemetry vs. a heavier dependency
(OpenLLMetry) or a separate hosted pane + data leaving the box (LangSmith). The httpx
spans already give per-call timing in the same Jaeger view.

**Trigger to build:** when prompt/completion/token-level analytics or LangSmith-based
evals become a priority.

## Separate the mock SIS's DB identity from `keel_app`

**Now:** each portal (mock SIS) connects to Postgres **as `keel_app`** — the same role
keel-api uses — *and* calls keel-api over HTTP for the widget mint-token. So the
portal has a dual data path and shares Keel's DB role. (Portal reads are now
RLS-scoped, D-R-015, so it can no longer see other tenants — but it is still
`keel_app`, so it *can* reach Keel-owned tables.)

**Deferred:** give the mock SIS its **own** DB role (read-only, RLS-bound, scoped to
the SIS-domain tables) distinct from `keel_app`, **or** route all Keel data through
keel-api so the portal has a single backend (HTTP only, no direct DB).

**Trade-off:** a cleaner SIS↔Keel boundary that matches the narrative (the SIS is the
university's system, not Keel's), at the cost of an extra role/grants or new
endpoints. Incremental — D-R-015 already removed the cross-tenant exposure.

**Trigger to build:** a real (non-mock) SIS integration, or a review that wants the
SIS physically unable to touch Keel-owned tables.

## Split the Keel console (operator app vs tenant-admin app)

**Now:** one role-based SPA at `/keel` on keel-api serves the platform operator **and**
both tenants' admins; a `platform_operator` JWT lands on operator views, a
`tenant_admin` JWT on admin views.

**Deferred:** separate the **platform-operator console** from the **tenant-admin
console** — ideally on different hosts — so operator auth is physically isolated from
tenant auth (smaller blast radius; an admin-surface bug can't reach operator routes).

**Does NOT enable tenant-scoping `keel_find_user_by_email`.** (Correcting an earlier
claim.) The operator/admin split alone keeps a single admin console serving *both*
tenants, so login is still tenant-unknown. What *would* let the **admin** login be
RLS-scoped is **per-tenant admin origins** (one host per university → host ⇒ tenant ⇒
set `app.tenant_id` before the lookup). Even then: Host is a weaker signal than a
per-tenant secret and needs per-tenant deploys, and the **operator** login is
tenant-less regardless (operator rows are `tenant_id IS NULL`, visible with no
bypass). So `keel_find_user_by_email` realistically stays cross-tenant.

**Trade-off:** stronger auth isolation (and, with per-tenant hosts, a smaller
BYPASSRLS surface) vs. more deploy surface and multiple origins to operate.

**Trigger to build:** production hardening of the operator/admin boundary, or a
multi-region/per-tenant deployment model.

## First-class saved/named Plan entity in the demo flow (A4 — UI surfacing)

**Now:** the agent proposes plans (`propose_plan`) and the student registers the plan
they picked **within the same conversation**. The `save_plan` / `load_plan` /
`activate_plan` tools and the `plans` table exist, but the demo registration flow does
**not** route through "save → list saved plans → re-validate → pick → register". Decided
2026-06-25 (see `specs/008-phase-5-frontend-widget-auth/registration-section-flow.md`).

**Deferred:** wire saved/named plans into the student-facing flow — list multiple saved
plans ("Fast Graduation", "Easy Semester", "Career-aligned"), re-validate each against
the current catalog on load (mark `STALE`), compare them, and **activate** one as the
plan of record, then register from it.

**Why it has value (and why it's invisible in a single demo session):** in a one-shot
"plan then immediately register" conversation, persistence adds nothing the student can
see — which is exactly why deferring it is safe. Its value appears across **time and
sessions**, and it is the anchor for three brief features:
- **Returning users / multi-term planning** — load a plan next week or next term without
  re-running the whole propose→verify→repair loop; compare strategies side by side.
- **Automatic replanning (A6)** — the background worker can only find and fix *saved*
  plans when a course/section/prereq changes. With no persisted plan there is nothing to
  monitor, invalidate, or notify on. Saved plans are the precondition for A6.
- **Course swap (A5) + the "exactly one active plan" invariant** — both operate on a
  persisted Plan entity; swap re-validates and updates it transactionally.

**Trade-off:** richer student journey (compare/activate/monitor) vs. more UI + a
"register a saved plan" pipeline and the load-time re-validation path. For the demo
spine (plan → sections → approve → enroll) it is pure addition, not on the critical path.

**Trigger to build:** demoing returning-user journeys, multi-term graduation planning,
or automatic replanning / alerts (A6/G1) — all of which need a persisted plan to act on.

## Automatic replanning (A6) and Personalized alerts (G1) — deferred by decision

**Decided 2026-06-26:** not built — judged low demo value for the effort.

**Now:** the worker runs `outbox_publisher`, `capacity_sync`, and the waitlist seat-open
path (`seat_open_notify` → outbox email). That seat-open trigger is the *one* alert-style
behaviour that exists today.

**Deferred:**
- **G1 (alerts):** a general `evaluate_alerts` worker job with the remaining ~3 triggers
  (eligibility-unlocked, risk-threshold-crossed, registration-window-opening), per-tenant
  RLS-scoped, deduped (one per student+trigger+subject) → notification rows + outbox.
  Infra exists (notifications table, outbox, scheduler); ~1 day. Low–medium risk
  (additive background work; main risk is notification storms, handled by dedup).
- **A6 (auto-replan):** worker that detects an invalidated saved plan → marks `STALE` →
  re-audits + re-plans → notifies. Building blocks exist (Plan entity, verifier, greedy
  planner) but it **mutates saved plans in the background** (correctness-sensitive: never
  silently rewrite the active plan) and **depends on the saved-plan flow** which is itself
  deferred above. ~1–2 days, medium risk.

**Trigger to build:** a deployment with returning users over time, where proactive
nudges and plan-invalidation actually accrue value.

## Carried from the capstone brief (STRETCH tier)

- ~~**Multilingual support** (Arabic / French / English)~~ — **DONE 2026-06-26.** Implemented
  as a system-prompt rule: the agent replies in the same language as the student's latest
  message (codes/IDs preserved; tool args stay English). The engine, models, and Cohere
  multilingual retrieval were already language-agnostic. Verified live in Arabic + French.
- **Career roadmap saved as a named plan** (E2 → A4).
- **Per-route thresholds** for write-capable intents — one global router threshold
  is fine for MVP.