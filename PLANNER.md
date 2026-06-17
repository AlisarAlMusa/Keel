# PLANNER.md — Plan Generation Architecture

How Keel generates, validates, and persists course plans. Read alongside
`ARCH.md` (overall system) and `SPEC.md` (tool contracts).

---

## Core invariant

> **Every plan shown to a student must have passed the verifier before it is
> displayed or persisted.** No plan produced by the LLM ever reaches the UI or
> the database without a `verify()` call returning zero violations.

This invariant is A4 in the spec. All plan-generating code paths must satisfy it.

---

## The propose → verify → repair loop

Used by: `propose_plan` (A1), `failure_recovery` (C3), `save_career_roadmap` (E2-save).

```
1. Engine builds the eligible course pool
   (audit() → eligible_now; filtered by program requirements + DAG)

2. LLM proposes 2–3 candidate plans
   (prompt lists the eligible pool — LLM cannot add courses outside it)

3. Engine validates each candidate
   verify(plan, catalog, graph, transcript, corequisites, term, year)
   → Violation[] (empty = valid)

4. If violations exist → LLM repair pass
   (structured violations fed back; LLM adjusts; re-verify)
   Retry cap: MAX_REPAIR_ROUNDS (default 2)

5. Greedy fallback
   If no LLM candidate is valid after repair rounds →
   greedy_plan(eligible_now, ...) produces a minimal valid plan
   (always valid by construction; never raises)

6. Predictors score valid plans
   graduation_risk_predict() + compute_workload() → badges

7. LLM ranks and explains valid candidates (feasibility + risk)
```

Implementation: `src/keel/agent/tools/planning.py::_validate_with_repair`

---

## C3 — Failure Recovery reuses the loop

`failure_recovery` (C3, `advising.py`) identifies courses a student failed or
withdrew from, then re-enters the same `_validate_with_repair` loop to propose
a recovery schedule.

Key differences from A1:
- **Seed** is the failed/withdrawn courses rather than the full eligible pool.
- **Engine re-audits** to confirm which of those are now re-eligible.
- **Greedy fallback** only includes re-eligible courses.
- Result is advisory text (C3 is read-only) — the student must explicitly call
  `propose_plan` or `save_plan` to persist anything.

---

## E2-save — Career Roadmap reuses the loop

`save_career_roadmap` (E2, `guidance.py`) seeds the loop with
`result.eligible_now[:4]` (up to 4 eligible courses) and routes through
`_validate_with_repair` before persisting.

An extra final `verify()` call is made at save time — if the plan was somehow
invalidated between the repair loop and persistence, it is not saved.

---

## A2 — Swap Course

`swap_course` proposes a single-course substitution, runs it through `verify()`,
and re-enters the repair loop if the swapped plan is invalid. The loop cap applies.
A2 re-enters the verifier on every candidate, so the saved plan is always valid.

---

## Greedy planner

Location: `src/keel/domain/engine/planner.py::greedy_plan`

Produces a term-by-term plan from the eligible pool by:
1. Topological sort of the DAG (prerequisites first).
2. Greedy course assignment to terms respecting credit cap.
3. No LLM, no network, no I/O.

The greedy planner is the fallback of last resort — it guarantees a valid plan
exists (or raises `NoPlanPossible` if the eligible pool is empty). The LLM
proposal loop runs first; the greedy planner only fires if all LLM candidates
fail validation after MAX_REPAIR_ROUNDS.

---

## What the engine never does

- Does not call the LLM.
- Does not read from the database (all data injected by the caller).
- Does not raise on bad input — it returns a `Violation[]`.
- Does not decide feasibility probabilistically (that is the risk model's job).

---

## Persistence (save_plan)

Only a plan that passed `verify()` inside the repair loop may be passed to
`save_plan`. The `save_plan` tool checks `plan.is_valid` before writing and
returns an error if False. The DB column `validated_at` is set to the
timestamp of the last successful verify call.

Idempotency: `save_plan` uses `ON CONFLICT (tenant_id, student_id, name, version)`
to avoid duplicate plan rows on retry.
