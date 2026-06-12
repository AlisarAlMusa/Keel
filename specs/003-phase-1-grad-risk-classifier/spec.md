# SPEC — Graduation-Risk Model (Keel · D1)

> **Rule:** This file is the source of truth. Code, the generator, the notebook, the
> engine, and the CI gate must match it. Any change here needs a `DECISIONS.md` entry.

---

## 1. Goal

Train **one** model. It looks at a `(student, candidate-plan)` pair and says
**on-track (0)** or **at-risk (1)**. The result feeds the LLM mitigation plan and the
risk badge on each plan.

This is the **second and last** trained model in Keel (first = intent classifier).
Workload (D2) stays deterministic. GPA (D3) stays an LLM baseline. No third trained model.

## 2. What it predicts

- **Unit:** a `(student-state, candidate-plan)` pair — not a student alone. The same
  student with two different plans must be able to get two different scores. If not, the
  badge is useless.
- **Target:** `at_risk ∈ {0, 1}`. `1` = at-risk = the minority class we care about.
- **Output:** a probability + a structured list of the top contributing features (derived
  from `feature_importances_` for RF/HistGB, or `coef_` for LR — extracted by the serving
  code after prediction, not returned by the model itself). The LLM writes the mitigation
  from that list. The LLM never recomputes the score.

## 3. Changes from the capstone brief (log both in `DECISIONS.md`)

| Brief said | We do | Why |
|---|---|---|
| Compare ML vs DL vs LLM | Compare **LR vs RandomForest vs HistGradientBoosting** | This is tabular data, not text. DL/LLM do not fit. We already showed the ML/DL/LLM lesson in the intent classifier. Comparing model families (linear / bagging / boosting) is the correct comparison here. |
| Imbalanced data + class-weight handling | **~25% at-risk at generation; balance fixed at training time** | A 50/50 synthetic set fails the "is your base rate realistic?" question. Real at-risk rates are a minority. We keep the data realistic and fix balance during training (`class_weight='balanced'`). |

Everything else from the brief (model count, no torch, propose→verify→predict→explain)
stays the same.

## 4. Feature contract (frozen)

### 4.1 The nine features — order is fixed

`FEATURE_ORDER` is exactly this, in this order. Model input, ONNX input, and
`feature_schema.json` all use this order.

| # | name | type | source | formula |
|---|---|---|---|---|
| 1 | `cumulative_gpa` | float 0–4 | transcript | credit-weighted GPA |
| 2 | `gpa_trend` | float | transcript | `mean(last TREND_WINDOW term GPAs) − cumulative_gpa` (negative = falling) |
| 3 | `num_failures` | int ≥0 | transcript | count of F or W grades |
| 4 | `num_repeats` | int ≥0 | transcript | count of courses taken more than once |
| 5 | `progress_rate` | float ≥0 | transcript + audit | `completed_credits / (terms_elapsed × EXPECTED_CREDITS_PER_TERM)` |
| 6 | `pct_complete` | float 0–1 | transcript + audit | `min(completed_credits / required_credits, 1.0)` |
| 7 | `planned_credits` | int ≥0 | candidate plan | sum of credits in the plan |
| 8 | `planned_workload_index` | float ≥0 | candidate plan | `sum(credits × difficulty)` — this is the D2 workload signal, reused |
| 9 | `num_hard_courses` | int ≥0 | candidate plan | count of plan courses with `difficulty ≥ HARD_DIFFICULTY_THRESHOLD` |

> **Two contexts, one contract.**
> - *In the generator (offline):* "candidate plan" means a fake plan sampled from the seed
>   catalog — random course picks whose credits and difficulty come from `_COURSES` in
>   `seed.py`. No real app, no real DB, just simulated inputs.
> - *In production (live app):* "candidate plan" means the real plan entity built by the
>   engine. The `predict_risk` tool reads actual courses from the DB, builds
>   `RawFeatureInputs`, and calls the same `compute_features`.
>
> The feature table defines the contract for **both**. The shared `compute_features`
> function is what makes both contexts produce identical vectors.

1–6 = student-state. 7–9 = plan-state. Both groups are required.

### 4.2 Fixed constants (same for generator and engine)

```
EXPECTED_CREDITS_PER_TERM = 15
TREND_WINDOW              = 2
HARD_DIFFICULTY_THRESHOLD = 4     # difficulty scale is 1..5
```

### 4.3 One feature function (the no-skew rule)

There is **one** function that turns raw inputs into the 9 features. The offline generator
and the online engine both call it. No second copy is allowed.

```python
# <backend>/domain/features/grad_risk.py

@dataclass(frozen=True)
class RawFeatureInputs:
    cumulative_gpa: float
    recent_term_gpas: list[float]        # oldest -> newest
    num_failures: int
    num_repeats: int
    completed_credits: float
    required_credits: float
    terms_elapsed: int
    plan_courses: list[tuple[int, int]]  # [(credits, difficulty), ...]

FEATURE_ORDER: list[str]                  # the 9 names above, in order

def compute_features(raw: RawFeatureInputs) -> dict[str, float]: ...
def to_vector(features: dict[str, float]) -> "np.ndarray": ...   # values in FEATURE_ORDER
```

`compute_features` must be pure, deterministic, and never crash on empty/odd inputs
(clamp, guard divide-by-zero with `max(denom, 1)`).

## 5. How labels are made

Labels are generated, not real. Honesty rules:

1. A documented **risk function** turns the 9 features into a risk number (logit).
   The exact weights live in `plan.md` and are copied into `DATA.md`.
2. The risk function **must include a nonlinear interaction** (weak student × heavy load),
   so the data is not a straight line. Without it, LR wins by default and the comparison
   means nothing.
3. `p = sigmoid(logit)`, then `at_risk = Bernoulli(p)`. **Sample it — do not threshold.**
   This sampling is the noise. It keeps the best possible F1 below 1.0.
4. Tune the intercept so the at-risk rate is **23–27%**.
5. `DATA.md` says clearly: the model learns the generator's idea of risk, not real-world
   risk. FERPA is why we do not use real transcripts.

## 6. Data

- **Rows:** ~4,000 → one file `grad_risk.csv` (full set).
- **Realism:** rows come from a latent-ability model, so they are believable (no 4.0 GPA
  with 5 failures). No feature is pure random noise.
- **Split:** done **in the notebook** — stratified 80/20 train/test, pinned seed. Tuning
  uses 5-fold stratified CV on the train part.
- **Columns:** `row_id`, the 9 features, `at_risk`.

## 7. Golden + honesty files (three different things)

| File | Size | Use | CI gate? |
|---|---|---|---|
| `grad_risk_test.csv` | the 20% test split (written by the notebook) | metric gate | **Yes** |
| `grad_risk_golden_edge.csv` | ~24 | hand-made obvious cases (clear at-risk / clear on-track) | **Yes** (all must be right) |
| `hand_labeled_slice.csv` | ~20 | you label these yourself; compare to generator labels in `DATA.md` | No |

## 8. Evaluation + CI gate

Report for all models. The gate checks the **winner**.

- **Main:** `macro_f1` (test).
- **Minority:** `at_risk_recall`.
- **Also report:** `on_track_recall`, `pr_auc`, Brier.
- **Trivial guard:** `macro_f1 < 0.97`. If higher, the data is too clean → fail.
- **Edge gate:** 100% correct on `grad_risk_golden_edge.csv`.

Starting thresholds (set final values after the first clean run, then commit):

```yaml
grad_risk:
  macro_f1_min: 0.72
  at_risk_recall_min: 0.70
  macro_f1_trivial_guard_max: 0.97
  edge_case_accuracy_min: 1.0
```

## 9. Artifacts to produce

| Artifact | Path | Note |
|---|---|---|
| Full dataset | `ml/grad_risk/data/grad_risk.csv` | from the generator |
| Golden edge set | `ml/grad_risk/data/grad_risk_golden_edge.csv` | from the generator |
| Hand-labeled slice | `ml/grad_risk/data/hand_labeled_slice.csv` | from the generator |
| Test split | `ml/grad_risk/data/grad_risk_test.csv` | written by the notebook |
| Model (serving) | `ml/grad_risk/artifacts/grad_risk.joblib` (+ `.onnx` if it converts) | joblib is the safe path |
| Feature schema | `ml/grad_risk/artifacts/feature_schema.json` | names + order + constants |
| Model card | `ml/grad_risk/artifacts/model_card.md` | task, data + SHA-256, 3-family results, choice |
| Eval report | `ml/grad_risk/artifacts/eval_report.json` | written every run, diffed |

## 10. Done checklist

- [ ] `compute_features` exists once, in the domain layer; generator imports it.
- [ ] `feature_schema.json` order == `FEATURE_ORDER` == model input order == ONNX input order.
- [ ] No leakage: split is stratified + seeded; `class_weight='balanced'` used at training time only.
- [ ] Noise is present: test `macro_f1 < 0.97`.
- [ ] All edge cases correct.
- [ ] All models (base + tuned) logged to MLflow; winner registered as **Production**.
- [ ] Served file SHA-256 matches the model card (model-server refuses to boot otherwise).
- [ ] Per-class recall reported, not just accuracy.
- [ ] CI gate green.

## 11. Non-goals

- No deep-learning model. No LLM baseline for this model.
- Binary target only (no multi-class).
- Workload (D2) stays deterministic.
- No MLflow drift / auto-retraining.