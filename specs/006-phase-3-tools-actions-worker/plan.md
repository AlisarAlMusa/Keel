# Keel — Phase 3 Day 4 PLAN

> HOW we build the Day-4 spec. Read `spec.md` first for WHAT + WHY.
> Standards apply: async everywhere, `Depends()` injection, lifespan singletons, typed boundaries,
> structured `ToolError` returns, structured + PII-redacted logging, transactional writes + outbox +
> idempotency, audit row on every write.

---

## 1. Technical approach

### 1.1 The `action` table (heart of the day)

```
action
  id              uuid pk          -- idempotency key
  tenant_id       fk               -- RLS scoped
  student_id      fk
  thread_id       text             -- LangGraph checkpoint thread to resume
  type            enum             -- enrollment | waitlist_join | waitlist_leave
                                   --   (Day 5: petition | major_change | graduation_app)
  payload         jsonb            -- FROZEN approved request (e.g. section_ids;
                                   --   for waitlist_join: {section_id, auto_enroll})
  status          enum             -- pending | approved | executed | rejected | failed | expired
  created_at      timestamptz
  decided_at      timestamptz null
  audit_ref       fk null
```

The `payload` is what gets written on resume — never re-read from the LLM.

### 1.2 Resumable agent (LangGraph + Postgres checkpointer)

- Agent graph runs on a **Postgres checkpointer**. `thread_id` = `tenant:student:request`. Resume verifies the thread belongs to the approving student (no cross-thread resume).
- Graph shape around a write:
  ```
  ... reason ...
  stage_node:     engine.validate(now) -> insert pending action -> action_id
  interrupt_node: interrupt({action_id, summary})   # suspends, checkpointed
  (human approves out-of-band -> resume)
  execute_node:   write tool bound to action_id (deterministic, approval-gated)
  ... continue with remaining tasks (bounded loop) ...
  ```
- Bounded loop (max iterations + token budget) still applies **after** resume.

### 1.3 Approve / reject handler (deterministic, non-agent)

`POST /actions/{id}/approve` runs three checks **before** touching the checkpointer, in this order:

```python
# 1. Load the action — RLS scopes the query to the current tenant.
#    A cross-tenant action_id returns nothing → 404, not 403. No information leak.
action = await action_repo.get(session, action_id)
if not action:
    raise HTTPException(404)

# 2. Student-isolation check — two students in the same tenant share the same
#    RLS scope, so RLS alone does NOT stop Student A from approving Student B's
#    pending enrollment. This explicit check is the student-level boundary.
if action.student_id != current_user.id:
    raise HTTPException(403, "Not your action")

# 3. Status guard — must still be pending.
if action.status != ActionStatus.pending:
    raise HTTPException(409, f"Action is {action.status.value}")

# All checks passed — safe to approve and resume.
# thread_id comes from the action row (written at stage time, bound to
# tenant+student), NEVER from the request — no cross-thread resume.
await action_repo.set_approved(session, action_id)
result = await graph.ainvoke(
    Command(resume={"action_id": str(action_id)}),
    config={"configurable": {"thread_id": action.thread_id}},
)
```

Why this ordering matters: RLS gives tenant isolation for free; the explicit `student_id` check is non-negotiable because RLS does not provide it inside a tenant. Both must pass; neither substitutes for the other. And the resume targets `action.thread_id` **read from the row**, never a request-supplied thread — the isolation checks gate the row, the row dictates which suspended run resumes, so a caller passing the checks on their own action still cannot resume another student's thread.

`POST /actions/{id}/reject` — same two-layer check → `status=rejected` → resume so the agent can re-plan or close cleanly.

### 1.4 Execute node (the only write)

On resume, bound to `action_id`:

1. Read the action; assert `status == approved`. Else refuse.
2. Re-validate with the engine (catalog may have changed). If invalid → `status=failed`, return explanation; agent may re-plan.
3. **Single transaction**:
   ```
   BEGIN
     insert domain row     (frozen payload; unique-constraint backstop)
     insert outbox row     (event: send confirmation email)
     insert audit row      (who/what/when/tenant)
     update action.status = executed
   COMMIT
   ```
4. Ignores any LLM-emitted write args — writes only the frozen `payload`.

### 1.5 Waitlist entity + outbox + worker

```
waitlist(id, tenant_id, student_id, section_id, position,
         auto_enroll bool, status enum,  -- waiting | fulfilled | failed | left
         created_at)

outbox(id, tenant_id, event_type, payload, processed=false, attempts=0, created_at)
   -- event_type: waitlist_joined | seat_open_notify
   --             | seat_filled_confirmation | seat_fill_failed | enrollment_confirmation
```

**Dual-caller:** `join_waitlist(student, section, auto_enroll)` / `leave_waitlist(...)` are deterministic service functions. The agent calls them as tools today; the portal button (with an "auto-enroll when a seat opens / just notify me" toggle) calls the same functions on Day 6. Same function, same verifier, same safety.

**Outbox = owed-work ledger; RQ = execution engine (composed, not alternatives).** The outbox row, written in the domain transaction, guarantees the side effect is owed even across crashes (no dual-write). The **outbox publisher** polls unprocessed rows and **enqueues an RQ job** carrying `outbox.id`; an **RQ worker** runs it with retry + exp backoff + concurrency, then marks `processed=true` **after** success. At-least-once; consumer dedupes on `outbox.id`.

**Seat-fill loop (capacity sync):** poll capacity → seat open → take waitlist student #1:
```
if auto_enroll:
    re-run verifier (catalog/holds/credits/conflicts may have changed)
    eligible -> txn { enroll + waitlist.status=fulfilled + outbox(seat_filled_confirmation) + audit }
    not      -> waitlist.status=failed + outbox(seat_fill_failed, reason) ; advance to student #2
else:
    outbox(seat_open_notify)   # classic notify; no write
```
Re-verification before any auto-enroll is mandatory — it is what makes delegated consent safe. Failure consumes the entry and notifies why; the seat is never wasted.

- **Expiry sweep**: `pending` actions older than TTL → `expired`; discard their suspended threads.

**Worker topology:** one worker process hosts all handlers — enqueued jobs (**RAG ingestion**, already wired) + scheduled loops (**outbox publisher**, **capacity sync + seat-fill**, **expiry sweep**). Not a service per job; handlers are independently splittable later if scaling demands it.

### 1.6 `predict_risk` (uses the ready `compute_features`)

```
backend assembles RawFeatureInputs(transcript, candidate_plan)
  -> compute_features(raw)            # SHARED module, single source of truth
  -> to_vector(features)              # FEATURE_ORDER, 9 features
  -> model_server POST /predict-risk  # joblib (sklearn) inference on the vector -> {label, score}
  -> derive reasons deterministically from salient features
  -> LLM writes mitigation plan from reasons
```

Model server stays lean: no DB, no feature logic, just inference. The same `compute_features` is used offline (generator) and online (here) — never duplicated.

### 1.7 `propose_plan` completion

```
engine.eligible_pool()
  -> LLM proposes <=3 candidates {label, courses[], term}
  -> per candidate: engine.validate(); drop/repair invalid (bounded)
  -> per valid: predict_risk(features) ; engine.workload_band()
  -> LLM ranks + explains (feasibility + risk + workload)
  (greedy fallback guarantees >=1 valid candidate when one exists)
```

### 1.8 Plan entity tools

- `save/load/activate/swap`: transactional + audit + RLS; **no outbox, no approval token**.
- One active plan: partial unique index `WHERE active = true` on `(student_id)`.
- `load` compares stored catalog version/hash; re-validates if changed.
- `swap` re-runs the verifier; idempotent update; reject + explain on invalidity.

### 1.9 Caching & rate limiting

- `fastapi-cache2` / Redis for catalog + tenant config; invalidation hook on admin catalog write (same event auto-replan uses on Day 6); short TTL backstop; per-tenant rate limit via Redis counter.

### 1.10 Model serving

- One production risk model, served directly (no promotion lifecycle). Keep SHA-256 pin + boot-refuse on mismatch; keep the (already-green) risk-F1 + at-risk-recall gate as a regression check; keep the model card.

---

## 2. Build sequence (dependency-ordered)

1. `action` table (+ `thread_id`) + Alembic migration; confirm `outbox` + `audit_log`.
2. Postgres checkpointer wired into the agent graph.
3. Action pattern: stage_node → interrupt_node → approve/reject handler (with resume) → execute_node.
4. `execute_enrollment` end-to-end (stage → interrupt → approve → resume → write+outbox+audit → continue).
5. **Write-action safety CI gate** (the moment the write path exists).
6. Outbox publisher worker + expiry sweep + email confirmation.
7. `predict_risk` wiring via `compute_features`.
8. `propose_plan` completion (multi-candidate + score + rank).
9. `join_waitlist` / `leave_waitlist` (dual-caller fns) + capacity sync + **seat-fill with re-verify** + the four waitlist outbox events.
10. Plan CRUD: `save/load/activate/swap`.
11. `simulate_whatif`.
12. GPA estimate.
13. Caching + per-tenant rate limiting.
14. Agent tool-selection CI gate.

Add each CI gate the moment the thing it tests exists.

---

## 3. Decisions (log into docs/DECISIONS.md)

- **Resumable agent (interrupt/resume), not stage-and-exit.** Handles multi-step requests where the write isn't terminal; justifies the Postgres checkpointer. Safety carried by the resume edge.
- **Frozen approved payload keyed by `action_id`.** The write ignores post-resume LLM args — closes the payload-swap injection risk.
- **Human-only resume.** Resume triggered solely by the authenticated approve endpoint; the LLM can't self-approve.
- **Two-layer isolation in the approve handler**: RLS provides tenant isolation; an explicit `action.student_id == current_user.id` check provides student isolation inside the tenant. Both run before the resume call; neither substitutes for the other.
- **Plan CRUD is not approval-gated and not outboxed** — student-owned, no institutional side effect.
- **No model promotion lifecycle.** One production model, already passed the gate; keep SHA pin + boot-refuse + regression gate + model card (per brief §13).
- **`compute_features` is the single source of truth**, shared by generator and `predict_risk`.
- **Delegated consent for waitlist auto-enroll.** Approval can be given in advance for a conditional action ("enroll me when a seat opens *and* I'm still eligible"). Valid consent because the verifier **re-runs at execution time**; on failure the action does not fire and the student is told why. Same pattern as a standing bank instruction, not blanket automation.
- **Dual-caller rule for all write actions.** Each write action is one deterministic service function with two callers — the portal (button) and the agent (tool). The function owns validation/txn/idempotency/outbox/audit; the agent adds language reach, never extra capability. (Portal caller wired Day 6; function built portal-ready now.)
- **Outbox + RQ composed, not either/or.** Outbox guarantees the side effect is owed (consistency, no dual-write); RQ executes it with retry/backoff/concurrency off the request path. Publisher only enqueues; `processed=true` set after success; consumer dedupes on `outbox.id`. One worker process, many handlers (incl. existing RAG ingestion).

---

## 4. Risks & mitigations

- **Resume reopens the write hole.** Mitigation: the four conditions (human-only resume, approval-gated executor, frozen payload, re-validate + idempotent); the write-action safety gate proves them.
- **Suspended threads accumulate.** Mitigation: expiry sweep worker.
- **Double-enroll / lost email.** Mitigation: `action_id` key + DB unique constraint; outbox in-transaction; worker retries; consumer dedupes.
- **Catalog changes during the wait.** Mitigation: execute node re-validates before writing.
- **Day 4 overload.** Mitigation: priority order; cut GPA → caching → `simulate_whatif`/`swap`; never cut the action pattern, the four safety conditions, the outbox, or the safety gate.
- **`propose_plan` cost/latency.** Mitigation: cap 3 candidates; score only valid; bounded repair.
- **Dependency on Day 2/3.** Engine + model + `compute_features` (ready) must be in place; verify before starting.

---

## 5. Definition of done

- Stage → interrupt → approve → resume → enrollment written in one transaction with outbox + audit → agent continues; re-approve / double-click never double-enrolls.
- Execute node refuses unless `status == approved`; writes the frozen payload only.
- Outbox worker emails confirmation with retry/backoff; expiry sweep runs.
- `predict_risk` uses shared `compute_features`; label + deterministic reasons + LLM mitigation; features never LLM-computed.
- `propose_plan` returns ranked, risk-scored, workload-banded, verifier-valid candidates only.
- Waitlist join/leave work through the same pattern; `auto_enroll=true` re-verifies student #1 on seat-open and either enrolls transactionally (+ confirmation) or marks `failed` (+ reason) and advances to #2; `auto_enroll=false` sends `seat_open_notify` only.
- Plan save/load/activate/swap work; one active plan enforced; load re-validates on catalog change.
- Caching with explicit invalidation + per-tenant rate limiting in place.
- One production model served with SHA pin + boot-refuse; risk regression gate green.
- Agent tool-selection gate and write-action safety gate both green.
- Injected "enroll me now" → unapproved pending action only, no write.