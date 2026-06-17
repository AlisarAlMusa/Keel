# Keel — Phase 3 Day 4 SPEC

> Phase 3 / Day 4 of the 8-day build.
> Goal: **plan → predict risk → student approves → enrollment executed, with outbox — then the agent continues.**
> First day Keel performs side-effecting writes. Most safety-critical day.
> This file is the contract (WHAT + WHY). `plan.md` is HOW. `tasks.md` is the ordered checklist.

Core principle (non-negotiable):
**Intelligence proposes. Deterministic systems verify. Models predict. Execution requires approval.**

---

## 0. Scope of Day 4

In scope:

- The single **action pattern** with a **resumable agent**: stage → interrupt → human approve → resume → transactional write + outbox + audit → continue.
- `execute_enrollment` (write) reached only on the approved-resume edge.
- `waitlist_join(section_id, auto_enroll)` / `waitlist_leave` on the same pattern — MVP, incl. **delegated auto-enroll on seat-open** (re-verify → enroll or fail-and-notify).
- RQ worker (one process, multiple handlers): outbox publisher (via RQ), capacity sync + **seat-fill with re-verify**, email-on-seat-open, **pending-action / suspended-thread expiry sweep**. RAG ingestion already lives here.
- `predict_risk` wired to the model server via the shared `compute_features`; mitigation from the LLM.
- `propose_plan` completed: multi-candidate, risk-scored, workload-banded, LLM-ranked.
- GPA estimate (LLM baseline, hard-caveated).
- `simulate_whatif`, `save` / `load` / `activate`, `swap_course` (Plan entity).
- Caching (catalog + tenant config) with explicit invalidation; per-tenant rate limiting.
- Two CI gates: agent tool-selection, write-action safety.

NOT today: institutional requests (Day 5, reuse the pattern) · advising/guidance chat (Day 5) · React/widget (Day 6) · auto-replan + alerts (Day 6) · neural reranker + multilingual (stretch).

---

## 1. The safety model for writes (the headline)

The agent **can** reach a write tool, but it can only fire on a **human-approved resume**. The guarantee is carried by the resume edge, not by hiding the tool.

Four conditions make this airtight:

1. **Human-only resume** — the graph resumes only via `POST /actions/{id}/approve`. The approve handler enforces **two isolation layers before touching the checkpointer**:
   - **Tenant isolation** — Postgres RLS scopes the `action` lookup to the current tenant. A cross-tenant `action_id` returns 404, not 403, leaking nothing.
   - **Student isolation** — the handler explicitly asserts `action.student_id == current_user.id`. Two students in the same tenant share the same RLS scope; this check is what stops Student A from approving Student B's pending enrollment. Return 403 on mismatch; do not resume.
   Both checks run in the approve handler, before `graph.ainvoke`. The LLM cannot resume or approve itself.
   - **Thread binding** — `stage_node` writes the `thread_id` (`tenant:student:request`) onto the action row, bound to `tenant_id` + `student_id` in that same row. On approve, the handler resumes **the thread read from the row** — never a `thread_id` supplied in the request. The two isolation checks gate access to the row; the row dictates which suspended run resumes. With no request-supplied thread to tamper with, a caller who passes the checks on their own action still cannot resume anyone else's suspended thread.
2. **Approval-gated executor** — the write tool refuses unless `action.status == approved`.
3. **Frozen payload keyed by `action_id`** — on resume the write executes the staged+approved payload read from the action row; it **ignores any write arguments the LLM emits after resume**. The human approved action X; the tool writes X's frozen payload, nothing else.
4. **Re-validate + idempotent** — on resume, re-run the engine (catalog may have shifted); if now invalid → `failed`, inform the student, agent may re-plan. Re-execute is a no-op (`action_id` key + DB unique constraint).

Isolation summary:

| Layer | Enforced by | Stops |
|---|---|---|
| Tenant isolation | Postgres RLS on `action` lookup | Cross-university data access |
| Student isolation | `action.student_id == current_user.id` in approve handler | Student A approving Student B's action |

Testable claim: *the write executor runs only on a human-approved resume of that student's own action, against the frozen approved payload — the LLM can neither approve nor alter what is written.*

### 1a. Delegated consent (pre-authorization) — for waitlist auto-enroll

The core principle requires **student consent** to the specific action — not that approval and execution happen in the same instant. Two readings both satisfy consent:

- **Immediate:** approval directly precedes execution (normal enrollment).
- **Delegated:** approval is given in advance for a conditional action ("enroll me if a seat opens *and* I'm still eligible"). The human approved action X under condition C; the system executes X only when C holds.

Delegated consent is the same pattern as a standing instruction in banking or a limit order in trading: real, specific, revocable consent — not blanket automation. It is **safe only because the verifier re-runs at execution time** (§3.1 waitlist): the seat may open days after approval, and eligibility can change. If re-verification fails, the action does **not** execute and the student is told why. The principle is intact: intelligence proposed, the student consented, the deterministic engine verified at execution, and execution honored that consent.

---

## 2. The action pattern (build once, reuse everywhere)

**Dual-caller rule (system-wide).** Every write action in Keel is a deterministic service function — it validates with the engine, writes transactionally, emits an outbox event, and audits. That function has **two callers**: the **portal** (student clicks a button, calls it directly) and the **agent** (student speaks, agent calls it as a tool). The function is the single source of truth for what the action does and how it stays safe; neither caller can bypass or weaken the validation, transaction, idempotency, outbox, or audit. The agent adds natural-language reach and multi-step reasoning — it never adds capability the portal lacks, and it is not a privileged backdoor. This applies to every write action: `execute_enrollment`, `waitlist_join`, `waitlist_leave`, `swap_course`, and the Day-5 institutional requests.

> Day-4 scope note: today builds the **function** (portal-ready: plain typed args, no agent-specific context) and the **agent caller**. The **portal button caller** is wired on Day 6 with the React surfaces. The rule holds now because the function is built portal-ready; the portal simply calls it later.

`action.status` lifecycle: `pending → approved → executed` (or `rejected`, `failed`, `expired`).

1. **Stage** (agent node): engine-validate **now** → insert `pending` action → return `action_id`. No write.
2. **Interrupt** (agent node): `interrupt({action_id, summary})` → graph suspends, state checkpointed in Postgres, control returns to the human with the plan + pending action.
3. **Human decision** (outside the agent): `POST /actions/{id}/approve` or `/reject`.
4. **Approve handler** (deterministic): verify ownership + `pending` → set `approved` (txn) → **resume the graph**.
5. **Execute node** (on resume): write tool bound to `action_id` → re-validate → single transaction { domain write + outbox row + audit row } → `executed`.
6. **Continue**: the agent proceeds with any remaining tasks under the bounded loop, then finishes.
7. **Outbox worker** publishes side effects (email, notifications); at-least-once; consumer dedupes.

Properties: idempotent (`action_id` key + DB unique constraints), atomic (write+outbox+audit commit together), audited (audit row per executed action).

Day-4 actions on this pattern: `execute_enrollment`, `waitlist_join`, `waitlist_leave`.
Day-5 actions (petition, major-change, graduation app) reuse it unchanged.

---

## 3. Tool / node contracts (Day 4)

All tools: async, Pydantic-validated, return a typed result or a structured `ToolError(error, retryable)` — never crash the agent. Every call is a trace span. The agent runs on a **Postgres checkpointer**; thread_id is scoped to tenant + student + request.

### 3.1 Staging + execute (the write path)

- `stage_enrollment(plan_id | section_ids)` → validate now → `pending` action (type=`enrollment`) → `{action_id, summary}`.
- `stage_waitlist_join(section_id, auto_enroll: bool)` / `stage_waitlist_leave(section_id)` → pending actions. The `auto_enroll` flag rides inside the **frozen payload** — `{section_id, auto_enroll}` — so the student's single approval covers both "put me on the list" and (when `auto_enroll=true`) "fulfill it for me when a seat opens and I'm still eligible." This is **delegated consent**, not a second approval: the conditional enrollment is pre-authorized by this one approval (see §1a).
- **Execute node** (post-approval only): reads the frozen approved payload by `action_id`, re-validates, performs the transactional write. Refuses if `status != approved`. Ignores LLM-supplied payload.

#### Waitlist behavior (MVP) — the course is the goal, the waitlist is the mechanism

A student who says "waitlist me" usually means "I want this course; give it to me if it becomes available." The waitlist row carries its own small state:

`waitlist_status ∈ {waiting, fulfilled, failed, left}` plus the `auto_enroll` flag.

**On join** (both `auto_enroll` values): transactional `waiting` row + audit + outbox event `waitlist_joined` (confirmation email).

**When a seat opens** (worker, see §4):
- `auto_enroll = false` → outbox event `seat_open_notify`: classic "a seat opened, register within the window" email. No write.
- `auto_enroll = true` → the worker **re-runs the verifier** for student #1 (the world changed since approval — new enrollments, credit cap, holds, prereq or time changes):
  - **eligible** → transactional { enrollment write + `waitlist_status=fulfilled` + outbox `seat_filled_confirmation` + audit }. The student gets the seat with no 2-hour race.
  - **not eligible** → mark `waitlist_status=failed`, outbox `seat_fill_failed` (email states *why*: e.g. credit cap, hold), **move to student #2**. The seat is never wasted, and the student always learns when delegated enrollment did **not** fire and why — silent failure would undermine the delegated-consent guarantee.

Re-verification at execution time is mandatory and is what keeps delegated consent safe. Four outbox event types cover every transition; the publisher delivers them via RQ (§4); event_type selects the email template — one mechanism, no special-case email code.

### 3.2 `predict_risk(student, plan)`

- Backend assembles `RawFeatureInputs` from transcript + candidate plan.
- Calls the **shared `compute_features`** (single source of truth; same module the offline generator uses) → `to_vector` in `FEATURE_ORDER`.
- 9 features: `cumulative_gpa, gpa_trend, num_failures, num_repeats, progress_rate, pct_complete, planned_credits, planned_workload_index, num_hard_courses`.
- Model server `/predict-risk` runs the production risk model — a **`.joblib`** artifact (sklearn) — on the vector → `{label, score}` (`label ∈ {on_track, at_risk}`).
- **Reasons** are derived **deterministically** from the salient feature values (not LLM-invented).
- LLM writes **only** the mitigation plan from those reasons. It does not compute features, decide the label, or invent reasons.

### 3.3 `propose_plan(constraints, preferences)` — completed

- Engine builds the eligible pool → LLM proposes **up to 3** candidates (balanced / graduation-focused / lighter) → engine validates each (bounded repair / greedy fallback) → **risk score only on valid candidates** + deterministic **workload band** → LLM **ranks + explains** using feasibility + risk + workload. Never returns a plan that fails the verifier.

### 3.4 `simulate_whatif(change)` — read-only

- Engine re-audits against the modified assumption → new timeline + credits-per-term; LLM explains. Never presents an infeasible alternative as feasible.

### 3.5 Plan entity tools (student-owned; no approval gate, no outbox)

- `save_plan` / `load_plan` (re-validate if catalog changed) / `activate_plan` (one active plan, partial unique index) / `swap_course` (engine re-validates, idempotent update, reject + explain on invalidity). Transactional + audit + RLS.

### 3.6 GPA estimate

- LLM baseline; response hard-caveated ("estimate, not a prediction"). Never a headline.

---

## 4. Background worker contract (one worker process, multiple handlers)

Keel runs **one worker process** that hosts several job handlers — not a service per job. It already exists (RAG ingestion was wired earlier); Day 4 adds handlers to it. Handlers are independent and can be split across processes later for scaling, but at this scale one process is correct.

The handlers are of two kinds:

- **Enqueued jobs** (something pushes a job): **RAG ingestion** — admin uploads catalog/policy docs → chunk → embed → write to pgvector. (Already built.)
- **Scheduled loops** (run on a timer):
  - **Outbox publisher** — see the outbox→RQ pipeline below.
  - **Capacity sync + seat-fill** — poll section capacity; on an open seat, take waitlist student #1; if their action was `auto_enroll`, re-run the verifier and either transactionally enroll (+ `seat_filled_confirmation`) or mark `failed` (+ `seat_fill_failed`) and move to #2; if `auto_enroll=false`, emit `seat_open_notify`. Never breaks the user-facing response.
  - **Expiry sweep** — expire stale `pending` actions + their suspended threads after a TTL.

### Outbox + RQ — two problems, composed (not alternatives)

- **Outbox = the owed-work ledger (consistency).** A row written in the same transaction as the domain write means the side effect is *owed* and will happen even across crashes. This is what prevents dual-write inconsistency between the DB and the email provider.
- **RQ = the execution engine.** The outbox publisher polls unprocessed rows and **enqueues an RQ job**; an RQ worker runs it with retry, exponential backoff, concurrency, and failure isolation — off the request path. The publisher's only job is to move owed work into the queue; RQ does the hard part.

Pipeline: `txn { domain write + outbox row }` → publisher polls → enqueue RQ job (carries `outbox.id`) → RQ worker sends → on success mark `processed=true`. At-least-once; the **consumer dedupes on `outbox.id`** (enrollment unique constraint backstops the enroll case). `processed=true` is set only **after** the job confirms success, never at enqueue time.

Structured, PII-redacted logging on every job. A publish/send failure never corrupts the committed write.

---

## 5. Caching & rate limiting

- Cache catalog reads + tenant config (Redis). **Explicit invalidation on catalog write** (TTL only as backstop) — a stale catalog produces wrong plans, so this is correctness. Per-tenant rate limit (Redis counter).

---

## 6. Model serving (no promotion ceremony)

- **One production risk model**, already passed the golden-set gate → `predict_risk` consumes it directly. No staging / provisional / promotion lifecycle (consistent with the brief §13: a full registry/promotion pipeline is overkill for two static models).
- Keep: **SHA-256 pin + model-server boot-refuse on mismatch** (integrity), the **risk-F1 + at-risk-recall gate as a committed regression check** (already green; fails CI on a regressing retrain), and the **model card** (data source + synthetic-honesty note).

---

## 7. CI gates added today (fail the build on regression)

1. **Agent tool-selection** — ~15 messages → expected tool (or correctly none). Gate on accuracy.
2. **Write-action safety** — five assertions, all must pass:
   - An unapproved or injected request produces no write.
   - The execute node refuses when `status != approved`.
   - The write uses the frozen payload — an LLM payload-swap after approval is ignored.
   - The LLM cannot self-resume.
   - **Student A cannot approve Student B's pending action (same tenant)** — approve handler returns 403, graph does not resume.
   One test suite, covers every current + future action because all share the pattern.

Thresholds in `eval_thresholds.yaml`; eval output diffed against last green build.

---

## 8. End-of-day acceptance (the Day-4 goal)

Message → router → agent → `propose_plan` returns ranked, risk-scored, valid candidates → `predict_risk` mitigation shown → student picks a plan → agent stages enrollment and **interrupts** → student clicks **Approve** → graph resumes → one transaction { enrollment + outbox + audit } → agent **continues** with any remaining task → worker emails confirmation. Re-approve / double-click never double-enrolls. An injected "enroll me now" leaves an unapproved pending action — no write. Both new CI gates green.

**Waitlist acceptance:** student approves a `waitlist_join` with `auto_enroll=true` → `waiting` row + `waitlist_joined` email → a seat opens → worker re-verifies student #1 → eligible: enrolled in one transaction with `seat_filled_confirmation` email; ineligible: marked `failed` with `seat_fill_failed` email stating why, and student #2 is considered. With `auto_enroll=false`, a seat-open sends `seat_open_notify` and writes nothing.