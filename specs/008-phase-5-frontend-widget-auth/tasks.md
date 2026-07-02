# Keel — Day 6 TASKS (granular, ordered for Claude Code)

Tags: **[CRIT]** critical path · **[CI]** gate · **[REAL]** functional · **[STAGE]** non-functional mock · **[THIN]** cuttable · **[SKIP]** don't build. Each task references `spec.md` (contracts) / `plan.md` (patterns).

---

## 0. Migrations (do before endpoints)
- [ ] **[CRIT]** Alembic: add `enrollments.source` enum default `'sis'`.
- [ ] **[CRIT]** Alembic: `usage_event` table (RLS-scoped) + RLS policy.
- [ ] Alembic: `notification` table (RLS-scoped) + RLS policy.
- [ ] Confirm `requests`, `outbox`, `audit_log` exist with `tenant_id` + RLS.

## A. Widget auth (do first) — see plan §2
- [ ] **[CRIT]** `POST /portal/login` — switcher → resolve tenant → set signed session cookie.
- [ ] **[CRIT]** `GET /portal/keel-token` — mint JWT `{tenant_id, student_id, aud, iat, exp(+900)}` from session; origin-check first; `student_id` from session only.
- [ ] **[CRIT]** `get_widget_context` dependency — verify signature/exp/aud → `WidgetContext`; `401` on failure.
- [ ] **[CRIT]** `db_with_tenant` dependency — `set_config('app.tenant_id', tid, true)`, yield, reset in `finally`.
- [ ] **[CRIT]** `verify_origin_or_403` — applied at mint and on `/chat`.
- [ ] **[CRIT]** Apply `get_widget_context` + `verify_origin` + `db_with_tenant` to `/chat` (and any student-scoped Keel route).
- [ ] Admin RLS dependency — set `app.tenant_id` from the registrar token; `require_role("tenant_admin")`.
- [ ] `GET /widget.js` loader — inject launcher, then iframe on open; reads `data-widget-id`.
- [ ] **[CRIT]** Lazy mint — widget fetches token on chat open; in-memory; silent refresh on `401`.
- [ ] **[CI]** Cross-tenant probe on a **pooled** connection: Tenant A token cannot read Tenant B.
- [ ] **[CI]** Disallowed origin → `403`; stale/raw token → `401`.

## B. Student widget UI (React/Vite)
- [ ] **[CRIT]** Streamed chat against existing `POST /chat` (SSE), token in header.
- [ ] **[CRIT]** Plan view with **risk + workload badges**.
- [ ] **[CRIT]** **Approval button** gating enrollment/requests.
- [ ] Save / compare / activate plans (existing Plan tools).
- [ ] Bundle small; served from API/MinIO with cache headers.

## C. Keel admin console (config only) — spec §3
- [ ] **[CRIT]** `POST /admin/rag/upload` (`catalog.md`,`policy.md`) → chunk → embed → tenant-tagged pgvector; return docs/chunks/last_upload.
- [ ] **[CRIT]** `GET/PUT /admin/widget-config` (persona, allowed_origins, enabled_tools). Safety rails NOT in payload.
- [ ] `GET /admin/widget-snippet`.
- [ ] **[THIN]** `GET /admin/cost?period=week` (GROUP BY over `usage_event`) + table UI.
- [ ] `GET /admin/audit` (read-only) + UI.
- [ ] **[SKIP]** request queue · catalog/student CRUD · rule editing.

## D. Mock SIS portal — student role — spec §4
- [ ] University-looking page hosting the embed; switcher wired to `/portal/login`.
- [ ] **[CRIT][REAL]** `GET /portal/schedule` — direct SIS-domain read; UI renders My Schedule.
- [ ] **[REAL]** `via Keel` badges from `enrollments.source`.
- [ ] **[REAL]** `GET /portal/requests` (status) + `GET /portal/activity`.
- [ ] **[STAGE]** Section-search UI + submit-petition form (visual only, no endpoints).
- [ ] **[SKIP]** manual Add / Drop.

## E. Mock SIS portal — registrar role — spec §4
- [ ] **[REAL]** `GET /portal/registrar/requests?status=pending` — queue.
- [ ] **[REAL]** `POST /portal/registrar/requests/{id}/decision` — approve/reject (transaction + outbox).
- [ ] **[THIN]** `GET /portal/registrar/catalog` · `/sections` — read-only seed renders + dead buttons.
- [ ] **[THIN]** `GET /portal/registrar/students` · `/rules` — read-only seed renders + dead buttons.

## F. Provenance & cost plumbing
- [ ] **[CRIT]** Enrollment write path sets `source='keel'`.
- [ ] **[CRIT]** Write a `usage_event` row on every LLM/embedding call.

## G. Workers (RQ/Redis) — spec §5
- [ ] Auto-replan: on catalog/section/prereq change → affected saved plans (scoped, deduped) → re-audit+plan → update/flag → outbox.
- [ ] Alerts: 4 triggers → `notification` rows → outbox; LLM phrases only.
- [ ] Reuse outbox publisher + email-on-seat-open (retry/backoff).

## H. Adapter (document only)
- [ ] `SISGateway` interface + `sis_integration` config captured in `docs/PRODUCTION.md`.
- [ ] **[THIN]** Grep raw cross-domain queries; note (don't change) for post-demo.

## I. CI gates (add as each exists)
- [ ] **[CI]** Write-action safety — no injected/unapproved request produces any write.
- [ ] **[CI]** Cross-tenant isolation on the auth path (pooled connection).
- [ ] **[CI]** Origin `403` + stale-token `401`.
- [ ] **[CI]** RLS reset proven — request for Tenant A on a reused connection cannot see Tenant B's leftover variable.

---

### Cut order if behind
1. Registrar Students/Rules → 2. Cost dashboard UI → 3. Auto-replan → 4. student stage-set pages.
**Never cut:** auth boundary · approval gate · My Schedule write-proof · registrar request queue · guardrails.

---

## Addendum tasks — operator, auth, second portal

> Merged from the former `missing_recovery.md` TASKS section. All implemented (Phase 5 addendum,
> 2026-06). Contracts: spec.md §10; rationale: DECISIONS D-A-001…005, D-R-008/011/015.

**Migrations**
- [x] **[CRIT]** `tenants.status`, nullable `users.tenant_id` + `'platform_operator'` role, check constraints, `platform_audit`, `platform_usage_summary`.
- [x] **[CRIT]** `keel_definer` (`NOLOGIN BYPASSRLS`) role bootstrapped in `scripts/db-init.sh`; migration 0011 reassigns the genuinely cross-tenant SECURITY DEFINER functions to it (D-R-008).
- [x] **[SEC]** Portal lookups RLS-scoped, not BYPASSRLS; migration 0012 drops the portal DEFINER functions (D-R-015).
- [x] **[CRIT]** `portal_user` table (portal-domain, RLS by `tenant_id`, unique email, FK→student).

**Auth + operator + portal**
- [x] **[CRIT]** Role-stamped token issue (operator token has no `tenant_id`); `require_role("platform_operator")`; `assert_tenant_active` on mint / `/chat` / admin login.
- [x] **[CRIT][REAL]** `/platform/tenants` list/provision/suspend/unsuspend/erase + `/platform/cost` (aggregate fn only) + `/platform/audit`.
- [x] **[CRIT][REAL]** `erase_tenant` RQ job (cascade by `tenant_id`, idempotent, audit counts).
- [x] **[CRIT][REAL]** `POST /portal/login {email,password}` (bcrypt, tenant-bound, generic 401); registrar login via same endpoint.
- [x] **[CRIT]** Second portal — one image, two compose services; each tenant's `allowed_origins` includes its portal origin.
- [x] **[CRIT]** Platform Console UI (login, tenants, cost, audit), role-gated.
- [x] **[SEED]** `seed.py` idempotent: 1 operator + 1 admin/tenant; 3 students + 1 registrar/tenant.

**CI gates**
- [x] **[CI]** Operator token → tenant-content routes rejected; cannot mint a widget token.
- [x] **[CI]** Suspend → mint/`/chat`/admin-login 403 while `/portal/login` + `/portal/schedule` stay 200; unsuspend restores.
- [x] **[CI]** Northane widget token cannot read Summit rows (pooled connection); wrong-tenant origin at mint → 403.

## Section-selection flow tasks

> Merged from the former `registration-section-flow.md`. Contract: spec.md §11; rationale:
> DECISIONS D-P6-001/002. Covered by `tests/unit/test_section_selection.py`.

- [x] **T1** `_after_stage` conditional edge routes a failed stage back to the LLM.
- [x] **T2** Migration `0013_section_instructor` + seed: 2 sections/course/offered-term, synthetic instructors, varied/full times; docs/DATA.md §1b honesty note.
- [x] **T3** `propose_sections` read tool (open-section pool + instructor/seats + pref fit; full vs not-offered) + section cards via the plan channel; system-prompt tool-error rule.
- [x] **T4** `stage_enrollment` preference-aware section resolution; `propose_plan` flags no-section courses.
- [x] **T5** Tests (pref ranking, conflict avoidance, unresolved, `_after_stage`) + write-safety/agent-node suites green.
- [ ] **Deferred** formatting-helper extraction from `propose_plan`; a richer structured `SectionCard` widget component (STRETCH).