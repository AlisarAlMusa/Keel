# Keel Deterministic Engine — TASKS

Ordered by dependency. This is the hardest day; build the verifier correct
**before** any feature touches it. `[GATE]` = CI gate added the moment the thing
it tests exists.

---

### T0 — Contracts & constants
- [ ] Write `contracts.py`: `Plan`, `Violation` (+ codes/scope), `AuditResult`
      (incl. student-state metrics), `Section`, `Requirement`.
- [ ] Confirm `domain/features/grad_risk.py` already holds `FEATURE_ORDER`,
      `RawFeatureInputs`, `compute_features`, `to_vector`, and the 3 constants
      (15 / 2 / 4) — it was built first and used by the generator. Engine imports
      from here; **no copy**.
- **Done when:** types import cleanly; engine reads the 3 constants from
      `grad_risk.py`.

### T1 — DAG layer  *(critical path)*
- [ ] Load prerequisites → graph; Kahn topo sort; cycle detection.
- **Done when:** valid catalog sorts; a cyclic catalog is rejected at load.

### T2 — Degree audit  *(critical path)*
- [ ] Transcript + program → remaining requirements, credits, `pct_complete`,
      `progress_rate`, `terms_elapsed`, `eligible_now`.
- **Done when:** audit matches hand-computed expected values on 2 seed
      transcripts.

### T3 — Verifier  *(critical path — the kernel)*
- [ ] Course-scope checks: PREREQ_MISSING, CREDIT_CAP_EXCEEDED, COREQ_MISSING,
      REPEAT_PASSED, NOT_OFFERED_THIS_TERM, NOT_ELIGIBLE, UNKNOWN_COURSE.
- [ ] Section-scope checks: TIME_CONFLICT (multi-meeting), CAPACITY_FULL.
- [ ] Scope flag selects which layers run. Returns `[Violation]`, never throws on
      well-formed input.
- **Done when:** one passing + one near-miss unit test per code.

### T4 — `[GATE]` Planner-correctness golden set  *(the headline gate)*
- [ ] ≥ 20 edge cases (SPEC §7) as data. Every seeded violation caught; every
      legal plan passes.
- [ ] Wire to CI; blocks merge on regression.
- **Done when:** gate is green and enforced.

### T5 — Section search
- [ ] Course set + schedule prefs → open, conflict-free section combinations
      (reuses the section-scope checks).
- **Done when:** returns only conflict-free, open combos on seed sections.

### T6 — Workload index
- [ ] `sum(difficulty × credits)` → raw index + light/medium/heavy band.
- **Done when:** bands match fixed thresholds on test plans.

### T7 — Greedy fallback planner
- [ ] Generate a verifier-valid plan from eligible-now, cap per term, validate
      each term; clean failure if none found.
- [ ] Determinism: same input → same plan.
- **Done when:** produces valid plans on seeds; determinism test passes.

### T8 — Risk-feature inputs (consume the shared module)
- [ ] `risk_inputs.py`: build `RawFeatureInputs` from audit + candidate plan;
      call `grad_risk.compute_features` / `to_vector`. Do **not** reimplement the
      math — it already exists and is what the generator used.
- [ ] In `predict_risk` (Phase 2), feed **one term's** courses as `plan_courses`
      (confirmed one-term unit; 3–7 courses in `seed.py`). For A2 path scoring
      (Phase 4): score each term separately, surface the worst — never flatten.
- [ ] `[GATE]` Guard tests: `len(FEATURE_ORDER)==9`; engine imports constants from
      `grad_risk.py` (no local copy); a hand-computed example matches; feature 8 ==
      `workload.py` raw index for the same courses.
- **Done when:** vector matches the hand-computed example; guard tests green.

---

## Build order (dependency)
`T0 → T1 → T2 → T3 → T4(gate) → T5 → T6 → T7 → T8(gate)`

## Day-2 milestone
Verifier proven correct on edge cases (T4 green), models training in parallel.
**Do not start any Day-3 feature until T4 is green** — the repair loop, registration,
prediction, swap, and replanning all assume a correct verifier.

## Never cut from this list
The verifier, the golden-set gate, the feature guard test. Everything else can be
trimmed; these are the floor.