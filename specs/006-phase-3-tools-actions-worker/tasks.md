# Keel — Phase 3 Day 4 TASKS

> Ordered, dependency-aware checklist. Read `spec.md` (WHAT) and `plan.md` (HOW) first.
> `[CRIT]` = blocks later work / the day's spine. `[CI]` = a gate that must go green.
> Add each CI gate the moment the thing it tests exists.

---

## A. Action pattern + resumable agent — the spine

- [ ] [CRIT] `action` table + Alembic migration (id, tenant_id, student_id, thread_id, type, payload, status, timestamps).
- [ ] [CRIT] Confirm `outbox` + `audit_log` tables exist (Day 1); add indexes if missing.
- [ ] [CRIT] Wire the **Postgres checkpointer** into the agent graph; thread_id = tenant:student:request.
- [ ] [CRIT] `stage_node`: Pydantic-validate → engine-validate now → insert `pending` action → return `action_id`. No write.
- [ ] [CRIT] `interrupt_node`: `interrupt({action_id, summary})` — graph suspends, state checkpointed.
- [ ] [CRIT] `POST /actions/{id}/approve` — enforce **two-layer isolation before resume**:
  - Layer 1 (tenant): load action via RLS-scoped query; cross-tenant `action_id` → 404.
  - Layer 2 (student): assert `action.student_id == current_user.id`; mismatch → 403 **without resuming**.
  - Status guard: action must be `pending`; else 409.
  - On all checks passed: set `approved` (txn) → `graph.ainvoke(Command(resume=...))`.
- [ ] [CRIT] `POST /actions/{id}/reject`: same two-layer isolation check → set `rejected` → resume so agent can re-plan/close.
- [ ] [CRIT] `execute_node` (post-approval only): assert `status==approved` → re-validate engine → single txn { domain write + outbox + audit } → `executed`. Reads FROZEN payload by action_id; ignores LLM-supplied args.
- [ ] Idempotency: re-approve of an `executed` action returns prior result; DB unique constraint `(student_id, section_id)` for enrollments.

## B. Enrollment + safety gate

- [ ] [CRIT] End-to-end: stage → interrupt → approve → resume → enrollment + outbox + audit committed atomically → agent continues.
- [ ] [CI] **Write-action safety gate** — five assertions, all must pass:
  - (a) Injected/unapproved request produces no write.
  - (b) `execute_node` refuses when `action.status != approved`.
  - (c) Write uses the frozen payload — an LLM payload-swap after resume is ignored.
  - (d) The LLM cannot self-resume.
  - (e) **Student A cannot approve Student B's pending action (same tenant)** → handler returns 403, graph does not resume, no write occurs.

## C. Worker (one process, multiple handlers — RAG ingestion already lives here)

- [ ] [CRIT] **Outbox publisher**: poll unprocessed → **enqueue RQ job** (carry `outbox.id`) → RQ worker sends confirmation email with retry + exp backoff + cap attempts → mark `processed=true` **after** success; consumer dedupes on `outbox.id`. (Outbox = owed-work ledger; RQ = execution engine — composed, not alternatives.)
- [ ] [CRIT] **Capacity sync + seat-fill** handler (see Section F).
- [ ] [CRIT] **Expiry sweep**: `pending` actions older than TTL → `expired`; discard suspended threads.
- [ ] All handlers run in **one worker process** (alongside existing RAG-ingestion job); independently splittable later — do not spin up a service per job.
- [ ] Structured, PII-redacted logging; publish/send failure never crashes the request path.

## D. Prediction

- [ ] [CRIT] `predict_risk`: assemble `RawFeatureInputs` (transcript + plan) → shared `compute_features` → `to_vector` (FEATURE_ORDER) → model server `/predict-risk` → `{label, score}` → deterministic reasons → LLM mitigation.
- [ ] Model server serves the single production risk model; SHA-256 pin + boot-refuse on mismatch.
- [ ] GPA estimate: LLM baseline, hard-caveated ("estimate, not a prediction").

## E. Planning suite

- [ ] [CRIT] Complete `propose_plan`: engine pool → ≤3 candidates → validate each (repair/greedy fallback) → risk score on valid only → workload band → LLM ranks + explains. Never returns an invalid plan.
- [ ] `simulate_whatif`: engine re-audits modified program → new timeline; LLM explains; read-only.
- [ ] `save_plan` / `load_plan` (re-validate if catalog changed) / `activate_plan` (one active plan, partial unique index).
- [ ] `swap_course`: engine re-validates edited plan → idempotent Plan update; reject + explain on invalidity.

## F. Waitlist (MVP — course is the goal, waitlist is the mechanism)

- [ ] `waitlist` table: `(id, tenant_id, student_id, section_id, position, auto_enroll, status{waiting|fulfilled|failed|left}, created_at)` + Alembic migration.
- [ ] `join_waitlist(section, auto_enroll)` / `leave_waitlist(section)` as **deterministic service functions** (dual-caller: agent tool today, portal button Day 6). `stage_waitlist_join` carries `{section_id, auto_enroll}` in the frozen payload through the action pattern (transactional + outbox `waitlist_joined`).
- [ ] Agent prompt: when a section is full, offer auto-enroll explicitly — "want me to waitlist you and enroll you automatically when a seat opens, if you're still eligible then?" One approval covers the conditional enrollment (delegated consent).
- [ ] Worker **seat-fill** (capacity sync): seat open → student #1 → if `auto_enroll`: re-run verifier → eligible: txn { enroll + status=fulfilled + outbox `seat_filled_confirmation` + audit }; ineligible: status=failed + outbox `seat_fill_failed` (reason) → advance to #2. If `!auto_enroll`: outbox `seat_open_notify`, no write.
- [ ] Re-verification before auto-enroll is mandatory; failure consumes the entry + notifies why; seat never wasted; never breaks the user-facing response.

## G. Caching & limits

- [ ] Cache catalog reads + tenant config (Redis); **explicit invalidation** on catalog write (TTL backstop).
- [ ] Per-tenant rate limiting (Redis counter).

## H. Final CI gate

- [ ] [CI] **Agent tool-selection gate**: golden set ~15 messages → expected tool (or correctly none); gate on accuracy.
- [ ] All thresholds in `eval_thresholds.yaml`; eval report diffed against last green build.
- [ ] [CI] Risk regression check (macro-F1 + at-risk recall) stays green on the golden set.

---

## Day-4 acceptance (must demo)

- [ ] One message → ranked, risk-scored, valid candidate plans returned.
- [ ] Student approves → agent resumes → enrollment written in one transaction with outbox + audit → agent continues with remaining task.
- [ ] Worker sends confirmation email.
- [ ] Double-click / re-approve does **not** double-enroll.
- [ ] Injected "enroll me now" → unapproved pending action only, no write.
- [ ] An LLM payload-swap after approval is ignored (frozen payload written).
- [ ] Waitlist: `auto_enroll=true` → seat opens → verifier re-runs on #1 → eligible enrolled in one txn with confirmation; ineligible #1 marked `failed` + told why, #2 considered. `auto_enroll=false` → seat-open notify only, no write.
- [ ] Both new CI gates green.

---

## Cut line (only if behind, in this exact order)

1. GPA estimate.
2. Caching + rate limiting → minimal.
3. `simulate_whatif`.
4. `swap_course`.

**Never cut:** the action pattern · the four write-safety conditions (human-only resume, approval-gated executor, frozen payload, re-validate + idempotent) · the outbox · the expiry sweep · the write-action safety gate.