# Keel Deterministic Engine — SPEC

> The engine is the spine. Roughly eight features ride on it. A leaky verifier
> undermines everything above it. This file is the **source of truth**: the
> contracts here are frozen before any code is written.

Principle: **Intelligence proposes. Deterministic systems verify. Models predict.
Execution requires approval.** The engine owns "verify."

---

## 1. Invariants (these are law)

1. **Pure.** No LLM calls, no network, no DB writes, no file I/O. Inputs in,
   values out.
2. **Deterministic.** Same input → same output. All ordering is stable (sort
   inputs; no random tie-breaks). This is what makes the golden-set CI gate
   possible.
3. **Never crashes on well-formed input.** A bad-but-valid-shape plan returns
   structured violations, never an exception. Only malformed input (unknown
   course code, missing field) returns a typed error — and even then prefer a
   violation (`UNKNOWN_COURSE`) over throwing.
4. **Structured output only.** The LLM repair loop reads machine-readable
   violation codes, not prose.
5. **One verifier, many producers.** Everything that creates or edits a plan
   (LLM, greedy planner, course swap, what-if, replanning, manual edit,
   registration) funnels through the same verifier. There is exactly one place
   that decides feasibility.

---

## 2. Scope boundary

**In scope (Day 2, pure compute):**
DAG load · degree audit · plan verifier · section search · workload index ·
greedy fallback planner · risk-feature extractor.

**Out of scope (Day 4, action layer — NOT the engine):**
- The idempotent, transactional **enrollment write**. The action layer *calls*
  the verifier (section scope) then writes. Keeps the engine 100% unit-testable
  with no DB.
- **Holds.** A hold blocks the *write*, not the plan. `HOLD_BLOCK` is a
  write-time gate, not a plan or section check.

---

## 3. Domain entities (shapes the engine reads)

```
Course      { code, name, credits, difficulty (1–5), offering_terms: [term_pattern] }
Prerequisite{ course_code, requires_code }            # edges of the DAG
Section     { section_id, course_code, term, capacity, enrolled,
              meetings: [ { day, start_min, end_min } ] }
Program     { program_id, total_credits, requirements: [Requirement] }
Transcript  { student_id, entries: [ { course_code, term, grade, credits } ] }
```

`difficulty` is load-bearing: it feeds the workload index **and** two risk
features. One field, three consumers — must stay consistent.

---

## 4. Contracts

### 4.1 Plan

A plan holds **courses per term**. Sections are resolved later by section search.

```
Plan {
  plan_id, tenant_id, student_id, program_id,
  name, version, active,
  terms: [ { term, course_codes: [code] } ],
  meta: { generated_by: "llm" | "greedy" | "manual", created_at }
}
```

Rationale: catalog/section churn does not invalidate saved plans; planning (A1)
stays clean of registration (B1).

### 4.2 Violation (the most consequential contract)

The repair loop and tests key off `code` + `detail`. `message` is UI-only.

```
Violation {
  code:    ViolationCode,
  scope:   "course" | "section",
  term:    term | null,
  courses: [code],          # offending course(s)
  detail:  { ... },         # structured, machine-readable
  message: string           # human-readable, UI only
}
```

`detail` must be repairable: `{ "missing_prereq": "CS101", "for": "CS201" }`
repairs; `"invalid plan"` does not.

**Codes by scope:**

| Code | Scope | Fires when |
|------|-------|------------|
| PREREQ_MISSING | course | a prereq is absent or scheduled in/after the same term |
| CREDIT_CAP_EXCEEDED | course | a term's credits exceed the cap |
| COREQ_MISSING | course | a corequisite is not present in the same term |
| REPEAT_PASSED | course | planning a course already passed |
| NOT_OFFERED_THIS_TERM | course | course not offered in the term it's placed |
| NOT_ELIGIBLE | course | program/eligibility block other than prereqs |
| UNKNOWN_COURSE | course | course code not in catalog (malformed input) |
| TIME_CONFLICT | section | two chosen sections' meetings overlap on a day |
| CAPACITY_FULL | section | chosen section has no open seat |

`HOLD_BLOCK` is **not** here — it is a Day-4 write-time gate.

> **docs/DECISIONS.md:** `REPEAT_PASSED` is hardcoded — a passed course cannot be re-planned. Grade-improvement repeats are a real institutional policy but add verifier complexity (tenant config threading) with no MVP value. Revisit if a real tenant requires it.

Empty violation list = valid.

### 4.3 AuditResult

```
AuditResult {
  completed_requirements:  [ { requirement_id, satisfied_by: [code] } ],
  remaining_requirements:  [ { requirement_id, type, still_needed } ],
  credits_completed, total_credits_required, remaining_credits,
  pct_complete,                  # = min(credits_completed / total_required, 1.0)
  progress_rate,                 # = credits_completed / (terms_elapsed * 15)
  terms_elapsed,
  eligible_now: [code],          # see 4.4
  # student-state metrics — feed RawFeatureInputs (§4.7), no second copy:
  cumulative_gpa, recent_term_gpas, num_failures, num_repeats
}
```

`pct_complete`/`progress_rate` here use the same constant and formula as
`grad_risk.compute_features`, so the displayed numbers and the model's features
agree by construction.

### 4.4 Eligible-now (single definition, reused everywhere)

A course is **eligible now** iff:
prereqs satisfied (DAG) **AND** offered this term **AND** not already passed
**AND** in the student's program.

No hold filter. Powers the planner, elective recommender, and the
"you can now take X" alert.

### 4.5 Section + time

`meeting = { day, start_min, end_min }`. Conflict = interval overlap on the same
day. A section may have multiple meetings (lecture + lab) — all are checked.

### 4.6 Requirement model (deliberately small)

```
Requirement =
    { type: "CORE",            courses: [code] }
  | { type: "ELECTIVE_GROUP",  choose: N, from: [code] }
  | { type: "CREDIT_FLOOR",    category, min_credits }
```

Plus `total_credits` on the program. No general rules DSL for MVP.

### 4.7 RiskFeatures (owned by the grad-risk track, consumed by the engine)

The feature math is **not the engine's** to define. It lives in one shared module,
`<backend>/domain/features/grad_risk.py`, which the offline generator already uses
(the models are trained). The engine must **not** reimplement it.

> **No second copy.** The generator (offline) and the engine (live) call the *same*
> `compute_features`. That is the only thing guaranteeing identical vectors in both
> contexts. A second implementation anywhere is a bug.

The 9 features, fixed order (`FEATURE_ORDER`):

| # | name | group | formula |
|---|------|-------|---------|
| 1 | cumulative_gpa | student | credit-weighted GPA |
| 2 | gpa_trend | student | `mean(last TREND_WINDOW term GPAs) − cumulative_gpa` (negative = falling) |
| 3 | num_failures | student | count of **F or W** grades |
| 4 | num_repeats | student | count of courses taken more than once |
| 5 | progress_rate | student | `completed_credits / (terms_elapsed × EXPECTED_CREDITS_PER_TERM)` |
| 6 | pct_complete | student | `min(completed_credits / required_credits, 1.0)` |
| 7 | planned_credits | term | sum of credits across **one term's** courses |
| 8 | planned_workload_index | term | `sum(credits × difficulty)` over one term — the **D2 workload signal, reused** |
| 9 | num_hard_courses | term | count of **one term's** courses with `difficulty ≥ HARD_DIFFICULTY_THRESHOLD` |

Constants (in `grad_risk.py`, single source — engine reads, never redefines):
`EXPECTED_CREDITS_PER_TERM = 15` · `TREND_WINDOW = 2` · `HARD_DIFFICULTY_THRESHOLD = 4`
(difficulty scale 1–5). Labels: `0 = on_track`, `1 = at_risk`.

**The engine's job (this is the contract the engine owns):** assemble
`RawFeatureInputs` from the audit + transcript + candidate plan, then call the
shared functions.

```
RawFeatureInputs {
  cumulative_gpa, recent_term_gpas (oldest→newest),
  num_failures, num_repeats,
  completed_credits, required_credits, terms_elapsed,   # from audit
  plan_courses: [ (credits, difficulty) ]               # ONE proposed term only
}
compute_features(raw) -> { name: value }      # owned by grad_risk.py
to_vector(features)   -> ordered 9-vector     # owned by grad_risk.py
```

Features 1–6 are student-state; 7–9 are term-state. Both groups required.
Prediction runs only on a verifier-valid candidate.

**Terminology — do not conflate two "plans":**
- The **Plan entity** (§4.1) is multi-term (a whole path of saved terms).
- `RawFeatureInputs.plan_courses` is **one term's** course list — nothing more.
The model never sees a multi-term path flattened into one vector.

**Scoring unit (confirmed against the generator):** one `(student-state,
one-term candidate-plan)` pair. The same student with two different next-term
plans must be able to get two different scores. The risk badge on a plan = the
score for the **next term's** registration, not a lifetime prediction.

**Single-term rule (CONFIRMED — this is a hard distribution boundary, not a soft
accuracy drop):** the generator trains on single-term plans. In this catalog one
term is 3–7 courses ≈ 9–21 credits; a full path is 90–120 credits, and the model
was standardized for the one-term ranges. Feeding a path-level list pushes
features 7–9 into extreme z-territory every time and the scores are garbage.

| feature | one-term range (valid) | full-path (wrong) |
|---------|------------------------|-------------------|
| planned_credits | 9–21 | 90–120 |
| planned_workload_index | ~15–80 | ~500–700 |
| num_hard_courses | 0–4 | 0–15 |

So `predict_risk` (Phase 2) passes exactly one term's courses. For **A2
Graduation Planning** (Phase 4, whole-path optimization): score **each term
independently**, then surface the **highest-risk term** — never aggregate the
path into one vector.

**Other consistency rules:**
- The engine imports the three constants from `grad_risk.py` — no local copy.
- Feature 8 must equal the D2 workload subsystem's raw index for the same courses;
  share one `sum(credits × difficulty)` helper, or assert equality in a test.
- `<2` recorded terms → `gpa_trend` returns 0.0 inside `compute_features` (empty
  guard + slice truncation); the engine just supplies whatever `recent_term_gpas`
  exist.

---

## 5. Component responsibilities

| Component | Input → Output |
|-----------|----------------|
| DAG loader | prerequisites → directed graph; topo sort; **reject cycles at load** |
| Degree audit | transcript + program → AuditResult (§4.3) |
| Verifier | plan (+ optional sections) + scope → [Violation] |
| Section search | course set + schedule prefs → open, conflict-free section combos |
| Workload index | plan → sum(difficulty × credits) → light/medium/heavy + raw index |
| Greedy planner | audit + horizon → a verifier-valid plan, or clean failure |
| Risk-feature inputs | audit + plan → `RawFeatureInputs`, then call `grad_risk.compute_features` (§4.7) — engine does not own the math |

---

## 6. Error & edge behavior

- Cyclic prerequisite catalog → reject at **load** with a typed error, never at
  query time.
- Unknown course in a plan → `UNKNOWN_COURSE` violation, not an exception.
- Infeasible what-if/target → returned as violations or "no valid path," never
  presented as feasible.
- Greedy planner cannot find a plan → clean failure (caller falls back / asks a
  human), never a partial/invalid plan.

---

## 7. Acceptance criteria ("correct" = the golden set)

The verifier must catch **every** seeded violation on a golden set of ≥ 20 edge
cases, and must pass every legal plan. The CI gate blocks merge on any
regression.

Required edge cases: missing prereq · prereq in same term · circular catalog
(load-time) · credit-cap overflow · missing corequisite · repeat of passed
course · course not offered in placed term · unknown course · (section scope)
time conflict including multi-meeting · full section. Plus N known-good plans
that must return zero violations.