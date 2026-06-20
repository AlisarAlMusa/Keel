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
- [ ] `SISGateway` interface + `sis_integration` config captured in `production.md`.
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