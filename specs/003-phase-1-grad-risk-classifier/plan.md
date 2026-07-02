# PLAN — How to build the Graduation-Risk Model

> Follows `spec.md`. If they ever disagree, **`spec.md` wins** — fix this file.
> Build in the order in §10. Log every real choice to `docs/DECISIONS.md` as you go.

---

## 0. Who runs what

| Step | Who | Where |
|---|---|---|
| Shared feature module | Claude Code | local repo |
| Data generator (write + run) | Claude Code | local repo |
| Notebook (write only — do **not** run) | Claude Code | local repo |
| Run the notebook | **You** | **Colab** |
| Pull artifacts back + CI gate | Claude Code | local repo |

The notebook runs in Colab, so it must be self-contained: install its own packages,
read the data file you upload, and connect to MLflow over the network.

## 1. Where files go

`<root>` = repo root. `<backend>` = the folder holding `api/services/repositories/domain/infra`.

**Before creating any folder or file, Claude Code must:**
1. Look at what already exists in the repo (`data/`, `training/`, `ml/`, `scripts/`, etc.)
2. Reuse existing directories where it makes sense — do not create a parallel structure
3. Look at the `ALL NOTEBOOKS` folder to understand the notebook style before writing a single cell

Likely mapping (verify against the real repo, adjust if structure differs):

```
<backend>/domain/features/grad_risk.py        # shared compute_features (build first)

<root>/scripts/
    generate_synthetic_data.py                # alongside seed.py — it imports from it

<root>/data/                                  # already exists (has intent data) — add here:
    grad_risk.csv
    grad_risk_golden_edge.csv
    hand_labeled_slice.csv
    grad_risk_test.csv                        # written by the notebook

<root>/training/                              # already exists — add here:
    train_grad_risk.ipynb                     # notebook — runs in Colab

<root>/ml/grad_risk/artifacts/               # create only if no equivalent exists
    grad_risk.joblib
    grad_risk.onnx                            # only if conversion works
    feature_schema.json
    model_card.md
    eval_report.json

<root>/tests/eval/test_grad_risk_gate.py      # CI gate
<root>/eval_thresholds.yaml                   # add the grad_risk: block
```

spec.md and plan.md live wherever the intent classifier's equivalent docs live — be consistent.

**Notebook style:** open every notebook in the existing `ALL NOTEBOOKS` (or `training/`)
folder first. Match their cell order, markdown headers, plotting style, variable naming,
and pipeline structure exactly. Do not invent a new style.

## 2. Shared feature module — build first

In `<backend>/domain/features/grad_risk.py`:

- Put the constants and `FEATURE_ORDER` from `spec.md` §4.2 / §4.1.
- Add `RawFeatureInputs` and `compute_features` and `to_vector` (spec §4.3).
- Write a few unit tests with hand-computed numbers. **Make them pass before moving on.**

## 3. Data generator (`generate_synthetic_data.py`) — run locally

Imports the shared function — does **not** re-implement it:
`from domain.features.grad_risk import compute_features, RawFeatureInputs, FEATURE_ORDER`
(install the backend with `pip install -e .`, or add `<backend>` to `sys.path` at the top).

### 3.1 Sample realistic rows (latent-ability model)

```
SEED = 42                              # one seed for everything

ability  ~ Normal(0,1)
momentum ~ Normal(0,1)
seniority ~ Uniform(0,1)

cumulative_gpa    = clip(2.6 + 0.55*ability + noise, 0, 4)
recent_term_gpas  = cumulative_gpa + 0.4*momentum + small per-term noise (TREND_WINDOW terms)
num_failures      = Poisson(softplus(0.8 - 0.9*ability))
num_repeats       = Binomial(num_failures, 0.6)
terms_elapsed     = round(1 + seniority*7)                       # 1..8
completed_credits = terms_elapsed * 15 * clip(0.75 + 0.3*ability, 0.5, 1.1)
required_credits  = 120

overload_tendency ~ Normal(-0.2*ability, 1)                      # weak students sometimes overload
planned_credits   = clip(round(15 + 3*overload_tendency), 9, 21)
plan_courses      = pick courses from the REAL seed catalog (read credits+difficulty from the
                    seed file, not a live DB) so sum(credits) ≈ planned_credits and harder
                    courses appear more when overload_tendency is high
```

Build `RawFeatureInputs`, then call the shared `compute_features`. Do not compute features
by hand here.

### 3.2 Risk function (copy weights into `docs/DATA.md`)

Standardize the 9 features (population mean/std), then:

```
logit = INTERCEPT
      - 1.2*z[cumulative_gpa]
      - 0.8*z[gpa_trend]
      + 1.0*z[num_failures]
      + 0.5*z[num_repeats]
      - 0.9*z[progress_rate]
      - 0.3*z[pct_complete]
      + 0.4*z[planned_credits]
      + 0.7*z[planned_workload_index]
      + 0.5*z[num_hard_courses]
      + 0.6*( relu(-z[cumulative_gpa]) * relu(z[planned_workload_index]) )   # interaction (required)

p = sigmoid(logit)
at_risk = Bernoulli(p)          # sample, do NOT threshold
```

Binary-search `INTERCEPT` so the at-risk rate is 23–27%.

### 3.3 Output

- Write `grad_risk.csv` (full ~4,000 rows: `row_id`, 9 features, `at_risk`).
- Write `grad_risk_golden_edge.csv` (~24 hand-set obvious rows, labeled by hand).
- Write `hand_labeled_slice.csv` (~20 rows + an empty `human_label` column + the generator
  label).
- Print: total rows, at-risk rate, and class means per feature. Check the rate is 23–27%
  and the classes overlap (not perfectly separated).

## 4. Notebook (`train_grad_risk.ipynb`) — written by Claude Code, run by you in Colab

Match the `ALL NOTEBOOKS` style. Order of cells:

1. **Setup** — install packages (`scikit-learn`, `mlflow`, `skl2onnx`,
   `onnxruntime`, `pandas`, `matplotlib`, `pyarrow`). Set `SEED`. Set MLflow env (see §5).
   Turn on `mlflow.sklearn.autolog()`.
2. **Load data** — read `grad_risk.csv` (you upload it to Colab, or read from Drive).
3. **EDA (simple)** — keep to ~4 plots: class balance bar, a couple of feature
   distributions split by class, and a correlation heatmap. Short markdown notes.
4. **Split** — stratified 80/20 train/test, pinned seed. Save the test set to
   `grad_risk_test.csv`.
5. **Preprocess (pipeline)** — all 9 features are numeric, so **no categorical encoding is
   needed**; add a short note saying so. Build the model as a standard **sklearn Pipeline**:
   `[StandardScaler, estimator]`. Pass `class_weight='balanced'` to LR and RF directly.
   For HistGB (no `class_weight` param), compute balanced sample weights before fitting:
   `sample_weight = compute_sample_weight('balanced', y_train)` and pass via
   `pipeline.fit(X_train, y_train, estimator__sample_weight=sample_weight)`.
6. **Base models** — train 3 pipelines with default params: LogisticRegression,
   RandomForestClassifier, HistGradientBoostingClassifier. Evaluate on test. Log to MLflow.
7. **Tune** — `RandomizedSearchCV` for each (StratifiedKFold=5, `scoring='f1_macro'`,
   `refit=True`, `random_state=SEED`). Reasonable small search spaces. autolog records the
   search params and CV results; also log the best params and best CV score yourself.
   Evaluate each tuned model on test. Log to MLflow.
8. **Compare all 6** — one table: base vs tuned × 3 families, with macro_f1,
   at_risk_recall, on_track_recall, pr_auc, Brier. Pick the **winner** by macro_f1,
   tie-break by at_risk_recall. The winner may be a base model — report it honestly.
9. **Safety checks** — assert `macro_f1 < 0.97` (trivial guard) and 100% on
   `grad_risk_golden_edge.csv`.
10. **Export** — save winner to `grad_risk.joblib`; try ONNX via `skl2onnx`
    (`grad_risk.onnx`) — if it fails for HistGB, skip ONNX and keep joblib. Write
    `feature_schema.json`, `model_card.md` (with SHA-256 of the served file), and
    `eval_report.json`.
11. **Register** — `mlflow.register_model` under name **`keel-grad-risk`**, then transition
    it to **Production**.

No torch, no transformers anywhere.

## 5. MLflow from Colab (do this before running)

Same setup as the intent classifier notebook. Before running:

1. Start your local Docker stack so MLflow is running.
2. Expose it with ngrok: `ngrok http <mlflow-port>` → copy the public URL.
3. In the notebook setup cell, set only:

```python
MLFLOW_TRACKING_URI = "https://<your-ngrok-url>"   # the ngrok public URL
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
```

No MinIO credentials needed in Colab — MLflow handles artifact upload to MinIO on the
server side automatically. This is exactly how the intent classifier was logged.

Log to MLflow:

- **params:** family, base vs tuned, seed, at-risk rate, full search space + best params.
- **metrics:** macro_f1, at_risk_recall, on_track_recall, pr_auc, Brier (for every model).
- **artifacts:** confusion matrix + PR curve plots, `feature_schema.json`, `eval_report.json`.
- **registry:** winner → `keel-grad-risk` → Production.

Note: lastly add a cell to download everything to the drive as a fallback.

## 6. Bring artifacts back + serving check

After the Colab run, download `grad_risk.joblib` (+ `.onnx`), `feature_schema.json`,
`model_card.md`, `eval_report.json`, and `grad_risk_test.csv` into `ml/grad_risk/...`
and commit them. The served file's SHA-256 must match the model card, or the model-server
will refuse to boot (existing behavior) — verify this.

## 7. CI gate (`tests/eval/test_grad_risk_gate.py`)

Loads thresholds from `eval_thresholds.yaml`, loads the served model + `feature_schema.json`,
loads `grad_risk_test.csv` and `grad_risk_golden_edge.csv`, then asserts:

1. `macro_f1 >= macro_f1_min`
2. `at_risk_recall >= at_risk_recall_min`
3. `macro_f1 <= macro_f1_trivial_guard_max`
4. edge-case accuracy `>= edge_case_accuracy_min`
5. `feature_schema` order == `FEATURE_ORDER` from the domain module

Add to `eval_thresholds.yaml`:

```yaml
grad_risk:
  macro_f1_min: 0.72
  at_risk_recall_min: 0.70
  macro_f1_trivial_guard_max: 0.97
  edge_case_accuracy_min: 1.0
```

After the first clean run, set `macro_f1_min` and `at_risk_recall_min` ~5 points below the
real scores and commit. Wire the gate into the GitHub Actions workflow next to the
intent-classifier gate.

## 8. Reproducibility

- One `SEED` everywhere (generation, split, every `random_state`).
- Pin package versions.
- The notebook must rerun top-to-bottom to the same numbers.

## 9. Log these to `docs/DECISIONS.md`

- LR/RF/HistGB instead of ML/DL/LLM (and why).
- Representative imbalance + `class_weight='balanced'` instead of SMOTE or 50/50 generation. Reason: at 25% minority rate, class weighting is sufficient; avoids the `imbalanced-learn` dependency and any risk of SMOTE leakage. `imbalanced-learn` is not installed anywhere in the project.
- The risk-function weights + the required interaction term.
- Tuning = RandomizedSearchCV, 5-fold, scoring f1_macro.
- Winner choice rule (macro_f1, tie-break at_risk_recall) and the actual winner.
- ONNX kept or skipped (and why).

## 10. Steps for Claude Code (in order)

1. **Feature module** — `<backend>/domain/features/grad_risk.py` + unit tests. Green first.
2. **Generator** — write `generate_synthetic_data.py`, **run it locally**, produce
   `grad_risk.csv` + golden edge + hand-labeled slice. Check the at-risk rate (23–27%)
   and class overlap. Log decisions.
3. **Notebook** — read the `ALL NOTEBOOKS` style, then write `train_grad_risk.ipynb` as in
   §4. **Do not run it.**
4. **(You) run the notebook in Colab** → MLflow logs → winner → Production → export
   artifacts → download `grad_risk_test.csv` + model + schema + card back into the repo.
5. **CI gate** — write `test_grad_risk_gate.py` + the `eval_thresholds.yaml` block, run it
   locally until green, then add it to GitHub Actions.

## 11. Bring Artifacts Back + CI (post-Colab)

After step 4 completes, the production model is registered in MLflow. Do these steps in order:

- [ ] **11a** `scripts/pull_model_artifacts.py` — write script that resolves `keel-grad-risk @ production`,
  downloads `grad_risk.joblib`, `grad_risk.onnx` (if present), `feature_schema.json`,
  `model_card.md`, `eval_report.json` to `ml/grad_risk/artifacts/`. Accepts `--dest` flag for
  container use. Fails loudly if MLflow is unreachable or model not found.

- [ ] **11b** Run `uv run python scripts/pull_model_artifacts.py` locally (needs `mlflow` + `minio`
  containers healthy). Confirm artifacts land in `ml/grad_risk/artifacts/`.

- [ ] **11c** Drag `grad_risk_test.csv` into `data/` (written by notebook, not logged to MLflow).

- [ ] **11d** Run `uv run pytest tests/eval/test_grad_risk_gate.py -v` — all 5 must pass.
  Update `eval_thresholds.yaml` `grad_risk` block with real scores (set min ~5 pts below actual).

- [ ] **11e** `git add ml/grad_risk/artifacts/ data/grad_risk_test.csv` and commit — unblocks CI
  (GitHub Actions cannot reach a local MLflow; committed artifacts are the CI source of truth).

- [ ] **11f** `docker-compose.yml` — add `model-artifacts` named volume; add `model-artifacts-sync`
  one-shot service (reuses `Dockerfile.api`, runs the pull script with `--dest /app/ml`,
  mounts the volume, depends on `mlflow: healthy`); update `model-server` to mount the same
  volume and depend on sync completing.

- [ ] **11g** `model-server/src/model_server/config.py` — add `grad_risk_artifacts_dir: str` setting.

- [ ] **11h** Smoke: `docker compose up -d --build` → confirm sync exits 0, model-server healthy.

- [ ] **11i** Commit all code changes and push to `002-phase-1-classifiers`.