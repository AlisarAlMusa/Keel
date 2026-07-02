# EVALS.md — Keel Evaluation Strategy

How Keel proves its AI and engineering components work correctly. Every gate below runs
in CI (`.github/workflows/ci.yml`) and blocks merge on regression — it is the mechanism
that keeps the project's core claim ("the LLM can never emit an invalid plan") provably
true. This document describes the gates **as they actually run today**; genuinely-deferred
evaluation ideas (RAGAS, report diffing) are called out in §5 and [`STRETCH.md`](STRETCH.md).

---

## 1. Three kinds of correctness

| Kind | What it means | How it's tested |
|------|--------------|-----------------|
| **Deterministic correctness** | The engine never approves an invalid plan and never rejects a valid one. | Golden-set unit tests with hand-written edge cases. Binary pass/fail. |
| **Model quality** | The intent classifier and graduation-risk model meet accuracy thresholds. | Held-out test sets, macro-F1, per-class recall, against committed artifacts. |
| **AI behaviour & safety** | The router picks the right node, guardrails hold, PII is redacted, no unapproved write executes. | Golden-set routing, adversarial probes, write-safety assertions. |

Thresholds live in [`../tests/eval/eval_thresholds.yaml`](../tests/eval/eval_thresholds.yaml).
Actual model numbers and the three-family comparison are committed in the model cards
(`ml/intent/artifacts/model_card.md`, `ml/grad_risk/artifacts/model_card.md`).

---

## 2. Gate overview (what CI enforces)

The `quality` job runs on every push/PR; the `smoke` job runs the full stack.

| # | Gate | Type | Metric / assertion | Location |
|---|------|------|--------------------|----------|
| 1 | Planner correctness | Deterministic | Every legal plan → `[]`; every broken plan → the expected violation | `tests/unit/test_engine_golden.py` |
| 2 | Intent classifier | Model | macro-F1 + covered-accuracy + 100% obvious-case golden | `tests/eval/test_intent_gate.py` |
| 3 | Graduation-risk | Model | macro-F1 + at-risk recall + 100% edge cases | `tests/eval/test_grad_risk_gate.py` |
| 4 | Tool selection | AI behaviour | Router sends write/read/chitchat intents to the correct node | `tests/eval/test_tool_selection.py` |
| 5 | Guardrails red-team | Security | 100% of injection + cross-tenant probes refused | `tests/eval/test_redteam_gate.py` |
| 6 | PII redaction | Security | Fake keys / emails / national IDs never appear unredacted | `tests/eval/test_pii_gate.py` |
| 7 | Write-action safety | Security | No injected/unapproved tool call reaches a DB write | `tests/unit/test_write_action_safety.py`, `tests/{unit,integration}/test_institutional_write_safety.py` |
| 8 | Stack smoke | Integration | compose up → healthy → migrate → RLS policies present → integration tests pass | CI `smoke` job |
| 9 | No-torch guardrail | Build | `torch` never appears in the model-server lockfile | CI `quality` job (grep) |

RAG has a **smoke** test (`tests/eval/test_rag_smoke.py`, 5 hand-written queries) that runs
only when `TEST_DATABASE_URL` + `COHERE_API_KEY` are present, and is skipped otherwise so the
unit CI job stays hermetic.

---

## 3. Gate details

### 3.1 Planner correctness (the headline gate)

**Proves:** the verifier catches every constraint violation and approves every valid plan.

`tests/unit/test_engine_golden.py` holds **25 hand-written cases — 17 broken plans (one per
violation type + boundary variants) and 8 legal plans.** Each broken plan asserts the exact
`ViolationCode`; each legal plan asserts `verify(...) == []`. Additionally, the greedy
planner's output is fed back through `verify()` — it must always produce a valid plan. One
failure fails the gate (binary).

Covered violation types: `PREREQ_NOT_MET`, `TIME_CONFLICT`, `SECTION_FULL`,
`CREDIT_CAP_EXCEEDED`, `COREQ_MISSING`, `HOLD_BLOCKS`, `NOT_OFFERED_THIS_TERM`,
`REPEAT_OF_PASSED`, `UNKNOWN_COURSE`, plus catalog-cycle detection (`CyclicCatalogError`)
and grade-floor / same-plan-prereq boundaries. Edge cases are **human-written**, not
generated — the engine is not "done" until they pass (CLAUDE.md §8, [`SPEC.md`](SPEC.md) §3.3).

### 3.2 Intent classifier

**Proves:** the trained router classifies messages correctly and its routing policy is safe.

Runs against the committed `ml/intent/artifacts/` (hermetic — no MLflow access). Reconstructs
the 175-row held-out test set from `data/intent_dataset.csv` + `data/intent-split.json`
(DECISIONS D-IC-002). Asserts (thresholds in `eval_thresholds.yaml`):

- `macro_f1_min: 0.75` (actual ≈ 0.803)
- `covered_accuracy_min: 0.87` — accuracy on messages above the router confidence threshold (actual ≈ 0.926)
- `golden_accuracy_min: 1.0` — all 30 obvious held-out cases correct (`data/intent_golden.csv`)
- `macro_f1_trivial_guard_max: 0.99` — a near-perfect score signals leakage, not skill
- label-map order and `model.classes_` cover exactly the 15 labels (guards the serving
  contract; DECISIONS D-IC-003/D-IC-004)

The three-way comparison (classical ML / small DL→ONNX / LLM zero-shot) is recorded in the
model card, with the deployment rationale in DECISIONS.

### 3.3 Graduation-risk model

**Proves:** the trained risk model identifies at-risk students (the minority class).

Runs against committed `ml/grad_risk/artifacts/` and `data/grad_risk_test.csv`. Asserts:

- `macro_f1_min: 0.77` (actual ≈ 0.822)
- `at_risk_recall_min: 0.68` (actual ≈ 0.731) — missing an at-risk student is worse than a false alarm
- `edge_case_accuracy_min: 1.0` on `data/grad_risk_golden_edge.csv`
- `macro_f1_trivial_guard_max: 0.97` — guards the required nonlinear interaction (see [`DATA.md`](DATA.md) §2)

Three-family comparison (linear / bagging / boosting) and the served SHA-256 are in the model
card. Class-imbalance handling: class weighting, documented in DATA.md.

### 3.4 Tool selection

**Proves:** the router/graph sends each intent to the correct node.

`tests/eval/test_tool_selection.py` is **offline** — it mocks the LLM response and asserts the
`should_continue` routing logic: write-intent calls → `stage`, read-intent calls → `tools`,
chitchat → `END`, and stage-tool names are identified without misrouting. Gate:
`tool_selection_accuracy: 0.80` on a 15-message golden set. (Multi-step conversations are
exercised in integration, not this gate.)

### 3.5 Guardrails red-team & PII

**Proves:** injection, cross-tenant probes, and PII exfiltration are all blocked.

- `tests/eval/test_redteam_gate.py` — every probe in the injection and cross-tenant sets must
  return `safe=False` (`injection_refuse_rate: 1.0`, `cross_tenant_refuse_rate: 1.0`). One
  pass = gate failure.
- `tests/eval/test_pii_gate.py` — `redact()` must eliminate email addresses, vendor-prefixed
  and generic API-key-shaped strings, and national-ID (SSN) patterns (`pii_leak_rate: 0.0`).

Probes are curated Python lists in the test files (not external fixtures). See
[`SECURITY.md`](SECURITY.md) §4 for the model behind them.

### 3.6 Write-action safety

**Proves:** no injected or unapproved tool call ever produces a write.

The action pattern is tested once and covers every action type ([`SECURITY.md`](SECURITY.md)
§5, §11):

- No agent tool schema exposes an `approved` field; every write service defaults to
  `approved=False`.
- `approved=False` → the DB session factory is never called (unit).
- A petition writes a `request_queue` row, never an `enrollments` row (integration).
- A cross-tenant filing is invisible to the other tenant; duplicate filings collapse to one
  PENDING row (integration).

### 3.7 Stack smoke (`smoke` job)

**Proves:** `docker compose up` from a clean checkout yields a working system.

Steps: pre-seed committed `ml/` artifacts into the model volume → `docker compose up -d
--build` → wait for `api` + `model-server` health → `alembic upgrade head` → **assert exactly
23 `tenant_isolation` RLS policies exist** → run `tests/integration/` against the live DB
(migration RLS, seed, widget-auth gates, institutional write safety) → tear down.

---

## 4. Threshold file

```yaml
# tests/eval/eval_thresholds.yaml (excerpt)
gates:
  intent:
    macro_f1_min: 0.75
    covered_accuracy_min: 0.87
    macro_f1_trivial_guard_max: 0.99
    golden_accuracy_min: 1.0
  grad_risk:
    macro_f1_min: 0.77
    at_risk_recall_min: 0.68
    macro_f1_trivial_guard_max: 0.97
    edge_case_accuracy_min: 1.0
  guardrails_redteam:
    injection_refuse_rate: 1.0
    cross_tenant_refuse_rate: 1.0
    pii_leak_rate: 0.0
  tool_selection_accuracy: 0.80
```

Thresholds sit ~0.05 below the measured values (headroom against noise) while still catching
a real regression. Raise them — never lower — when a model improves, and record the change
in [`DECISIONS.md`](DECISIONS.md).

---

## 5. How to run evals locally

```bash
uv run pytest tests/unit/test_engine_golden.py -v   # planner correctness
uv run pytest tests/eval -q                          # intent · grad-risk · redteam · PII · tools
uv run pytest tests/unit -q                          # full engine + safety suite

# RAG smoke + integration need a live seeded stack:
docker compose up -d --build
docker compose exec api uv run alembic upgrade head
docker compose exec api uv run python -m scripts.seed
TEST_DATABASE_URL=postgresql+asyncpg://keel_app:change-me@localhost:5432/keel \
COHERE_API_KEY=... uv run pytest tests/eval/test_rag_smoke.py tests/integration -q
```

> There is no `Makefile`; run the `pytest` targets directly. Model artifacts are committed
> under `ml/`, so the model gates are hermetic and need no MLflow access.

---

## 6. What evaluation does NOT cover (honesty)

- **RAGAS faithfulness/relevancy scoring and an eval-report diff to MinIO** — designed but
  **not built**. RAG is checked today by the 5-query smoke test plus the DAG-grounding
  invariant (a stated prerequisite must exist in the graph). See [`STRETCH.md`](STRETCH.md).
- **Subjective answer quality** (is the explanation *good*?) — reviewed manually in
  development; an LLM-as-judge rating is a stretch goal, not a gate.
- **Latency / load / stress** — documented as scaling considerations in
  [`DESIGN.md`](DESIGN.md), not gated in CI.
- **Risk-model drift** — meaningful only with real data over time; with synthetic data it is
  a production consideration, not a demo concern.
