# Keel Deterministic Engine — PLAN

How we build what SPEC.md defines. Decisions, algorithms, layout, testing.

---

## 1. Architecture & placement

The engine is a **pure functional core** with no infrastructure dependencies. It
lives in the `domain` layer; thin `services` wrap it to fetch data and call it.
Nothing in the engine imports the DB client, the LLM client, or the network.

```
api/         -> HTTP, auth, request/response
services/    -> orchestration: load data, call engine, return results
domain/      -> THE ENGINE (pure). dag, audit, verifier, sections,
                workload, planner, features
infra/       -> db, cache, model-server client, outbox (engine never imports these)
```

Why: a pure core is fully unit-testable with in-memory fixtures and no
containers. The golden-set gate runs in milliseconds and is reproducible.

## 2. One verifier, many producers

The verifier is the kernel. A `scope` flag selects which check layers run:
- `scope=course` → course checks (plan validation).
- `scope=section` → course checks + section checks (registration validation).

Every producer — LLM proposal, greedy planner, swap, what-if, replan, manual
edit, registration — calls the **same** verifier. One source of truth for
feasibility, so adding a producer adds zero new feasibility logic.

## 3. Algorithms (kept simple and exact)

- **DAG load:** build adjacency from the prerequisites table. Topological sort
  via **Kahn's algorithm**; if nodes remain after the queue drains, there's a
  cycle → reject at load.
- **Prereq check:** a course is satisfiable in term *t* iff every prereq is
  completed (transcript) or placed in a term **before** *t*.
- **Time conflict:** per day, sort meetings by start; two overlap iff
  `a.start < b.end AND b.start < a.end`. O(n log n) per term — trivial at this
  scale, no bitmask needed.
- **Credit / coreq / repeat / offering / eligibility:** direct table lookups and
  per-term aggregation. Each emits its own violation code.
- **Audit:** diff program requirements against transcript → remaining
  requirements, credits, `pct_complete`, `progress_rate`, and the eligible-now
  set (§4.4 of SPEC).
- **Workload:** `sum(difficulty × credits)` per term → raw index + band by fixed
  thresholds (thresholds pinned in config, justified in DECISIONS.md).
- **Greedy planner:** from the eligible-now pool, repeatedly pick the
  highest-priority eligible course (priority = unlocks-most-downstream, then
  required-before-elective, stable tie-break by code) until the credit cap is
  reached; advance term; **validate with the verifier each term**. Goal:
  produce *a* valid plan or fail cleanly — not the optimal plan.
- **Risk features:** build `RawFeatureInputs` from audit + candidate plan, then
  call the shared `compute_features` / `to_vector` in
  `domain/features/grad_risk.py` (already built and used by the generator — **no
  second copy**). Feature 8 reuses the workload helper.

## 4. Key decisions & tradeoffs

| Decision | Choice | Tradeoff accepted |
|----------|--------|-------------------|
| Plan content | courses per term; sections resolved at registration | plan can't show a room/time until registration — fine for planning |
| Enrollment write | Day-4 action layer, not the engine | engine can't write — by design; keeps it pure/testable |
| Requirement model | CORE + ELECTIVE_GROUP(choose N) + CREDIT_FLOOR | can't model exotic rules — out of MVP scope |
| Time model | per-day (day, start_min, end_min) overlap | none meaningful at this scale |
| Greedy planner | valid-or-fail, not optimal | fallback may miss a plan that exists — acceptable for a fallback |
| Holds | write-time gate, not a plan/section check | planning ignores holds; the write enforces them |
| Verifier shape | one engine, scope flag | none — this is the unification win |
| Risk features | engine builds `RawFeatureInputs`; math lives in `domain/features/grad_risk.py` (one copy) | engine must not redefine formulas/constants — guard test enforces it |
| Risk scoring unit | `(student-state, one-term plan)`; `predict_risk` feeds **one term** (confirmed: 3–7 courses in `seed.py`) | A2 must score each term separately and surface the worst — never flatten the path |

## 5. Module layout

```
domain/features/
  grad_risk.py    # FEATURE_ORDER, constants, RawFeatureInputs,
                  # compute_features, to_vector  (single source — already built)
domain/engine/
  graph.py        # DAG load, Kahn topo sort, cycle detection
  audit.py        # degree audit, eligible-now, student-state metrics
  verifier.py     # check layers, scope flag, Violation assembly
  sections.py     # section search, time-conflict detection
  workload.py     # difficulty aggregation, bands (raw index helper reused by feature 8)
  planner.py      # greedy fallback planner (uses verifier)
  risk_inputs.py  # builds RawFeatureInputs from audit + plan, calls grad_risk
  contracts.py    # typed shapes: Plan, Violation, AuditResult, Section, Requirement
```

The engine **imports** `grad_risk.py`; it never copies its formulas or constants.

## 6. Testing strategy

- **Unit-test the verifier hardest.** One test per violation code: a plan that
  must trigger it, and a near-miss that must not.
- **Golden set (≥ 20 edge cases)** per SPEC §7 → the CI gate. Stored as data,
  not code, so cases are easy to add.
- **Determinism test:** run the planner twice on the same input, assert
  identical output.
- **Cycle test:** a cyclic catalog must fail at load.
- **Feature guard test (no-skew trip-wire):** assert `len(FEATURE_ORDER) == 9`
  and `to_vector` follows that order; assert the engine imports constants from
  `grad_risk.py` (no local copy); and assert the engine's `RawFeatureInputs` →
  `compute_features` matches a hand-computed example. Plus: feature 8 must equal
  the `workload.py` raw index for the same courses.
- Optional: property-based tests (any valid plan → zero violations; any plan +
  one injected conflict → exactly that violation).

## 7. Integration points

- **Verifier → repair loop (Day 3+):** verifier returns structured violations;
  the LLM repairs from `code` + `detail`; re-validate until clean.
- **Audit + plan → `RawFeatureInputs` → `grad_risk.compute_features` →
  model-server (Day 4):** engine builds the raw inputs (feeding **one term's**
  courses as `plan_courses`), the shared module produces the vector, the
  model-server scores it, the LLM writes the mitigation.
- **Eligible-now → planner, elective recommender (E1), alerts (G1):** one
  definition, three consumers.

## 8. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Verifier bug → everything above is wrong | build + test the verifier first; golden gate before any feature |
| Feature drift vs trained model | one shared `grad_risk.py`; engine imports its constants; guard test in CI |
| Plan-scale mismatch (features 7–9 out of distribution) | confirmed one-term unit; `predict_risk` feeds one term; A2 scores per term and surfaces the worst |
| Greedy planner loops or returns invalid | hard term cap + verify-each-term + clean-fail contract |
| Requirement model creeps into a DSL | freeze the 3 types in SPEC; new types need a decision entry |
| Catalog cycle crashes at query time | detect at load, reject with typed error |