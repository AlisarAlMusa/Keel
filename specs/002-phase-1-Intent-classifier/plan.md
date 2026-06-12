# PLAN — How the Intent Classifier & Router was built

> Follows `spec.md`. If they ever disagree, **`spec.md` wins** — fix this file.
> Documents what shipped (the model is already trained and registered). The remaining
> open work is the "Bring Artifacts Back + CI" section (§7), mirroring grad-risk's §11.

---

## 0. Who runs what

| Step | Who | Where |
|---|---|---|
| Dataset generator (write + run) | Claude Code | local repo |
| Notebook (write only — do **not** run) | Claude Code | local repo |
| Run the notebook (train A/B/C, register winner) | **You** | **Colab** |
| Pull artifacts back + CI gate | Claude Code | local repo |

The notebook runs in Colab, so it is self-contained: installs its own packages, reads the data
files you upload, and connects to MLflow over the network (ngrok).

## 1. Where files go

```
scripts/
    generate_intent_dataset.py        # hand-authored seeds → 1,050 rows + split

data/                                 # already exists — outputs land here:
    intent_dataset.csv                # text,label,seed_group_id (1,050 rows)
    intent-split.json                 # grouped, stratified 80/20 split

training/
    train_intent_classifier.ipynb     # notebook — runs in Colab

ml/intent/artifacts/                  # pulled from MLflow + committed for CI:
    model_a.joblib                    # served model (Model A = production)
    label_map.json
    router_config.json
    metrics.json
    model_card.md                     # authored locally

tests/eval/test_intent_gate.py        # CI gate
tests/eval/eval_thresholds.yaml       # add the intent: block
```

## 2. Dataset generator (`scripts/generate_intent_dataset.py`) — run locally

- `LABELS` = the 15 frozen labels (spec §4), canonical order.
- `SEEDS[label]` = list of seed ideas; each seed is a list of ~6 paraphrases that all mean the
  same intent. All paraphrases of one seed share a `seed_group_id`.
- `RANDOM_SEED = 42`, `EXAMPLES_PER_LABEL = 70`, `TEST_FRACTION = 0.20`,
  `NEAR_DUP_JACCARD = 0.92`.
- `build_rows()` expands seeds → rows, dedups globally (exact + Jaccard), trims each label to
  exactly 70 (dropping from the largest seed groups first).
- `make_split()` does the grouped, label-stratified 80/20 split and **asserts** the train/test
  `seed_group_id` sets are disjoint (no paraphrase leakage).
- Outputs `data/intent_dataset.csv` and `data/intent-split.json`. Prints per-label counts and
  the leakage-free guarantee.

## 3. Notebook (`train_intent_classifier.ipynb`) — written by Claude Code, run by you in Colab

Cell order (already built):

1. **Setup** — install packages; set `SEED`, `LABELS`, `LABEL2ID`/`ID2LABEL`, MLflow env.
2. **Data files** — upload `intent_dataset.csv` + `intent-split.json` to `/content/data/`.
3. **Connect to MLflow** — `ngrok http 5001` on your Mac → paste the public URL.
4. **Light EDA** — class distribution, message lengths, samples/label, data quality.
5. **Load split** — read the grouped split; confirm no `seed_group_id` on both sides.
6. **Model A — TF-IDF + LR** — `FeatureUnion(word 1–2gram, char_wb 3–5gram)` → LR. Log to MLflow.
7. **Model B — DistilBERT → ONNX** — fine-tune, export to a single ONNX file, verify ONNX
   matches PyTorch within tolerance, infer via `onnxruntime`. Log to MLflow.
8. **Model C — Gemini zero-shot** — reference baseline; logged but **never registered**.
9. **Comparison** — macro-F1 / accuracy / latency / cost table; grouped bar; confusion matrices.
10. **Winner selection** — B beats A only if macro-F1 margin > 0.005, else A.
11. **Threshold selection** — sweep thresholds; pick the lowest with accuracy-on-covered ≥
    target; save `router_config.json` (separate artifact).
12. **Register** — `register_model` → `keel-intent-router`, set alias `production`.
13. **Companion artifacts** — log `label_map.json`, `router_config.json`, `metrics.json`;
    print SHA-256 checksums for the model card.
14. **Backup** — fallback cell to copy artifacts to Google Drive before the runtime resets.

No torch at inference (ONNX only). Model C is never deployable.

## 4. MLflow from Colab

Same setup as grad-risk: start the local Docker stack so MLflow is up, expose it with
`ngrok http 5001`, paste the public URL into `MLFLOW_TRACKING_URI`. No MinIO credentials needed
in Colab — MLflow's `--serve-artifacts` proxies the upload server-side.

Logged: params (model type, n-gram ranges, LR `C`, train/test sizes), metrics (macro_f1,
accuracy, latency, cost), artifacts (`model_a.joblib`, `label_map.json`, `router_config.json`,
`metrics.json`, confusion matrices), registry (winner → `keel-intent-router` → `production`).

## 5. Reproducibility

- One `SEED = 42` everywhere (generation, split, every `random_state`).
- The grouped split is deterministic and pinned in `intent-split.json`.
- Pin package versions in the notebook setup cell.

## 6. Log these to `DECISIONS.md`

- TF-IDF+LR vs DistilBERT vs Gemini (the ML/DL/LLM three-way) and why text justifies it.
- 13 → 15 labels (`my_info`, `chitchat` added) and why.
- Grouped-by-`seed_group_id` split to prevent paraphrase leakage.
- Winner rule (margin > 0.005 favors B, else A) and the actual winner (A).
- Threshold lives in `router_config.json`, outside the model.

## 7. Bring Artifacts Back + CI (post-Colab)

The production model is registered in MLflow as `keel-intent-router @ production`. Do these in
order (mirrors `specs/003-…/plan.md` §11):

- [ ] **7a** Generalize `scripts/pull_model_artifacts.py` to a MODELS registry so it resolves
  **both** `keel-grad-risk` and `keel-intent-router` at alias `production` and downloads each
  run's `artifacts/` to `<dest>/<subdir>/artifacts/`. Intent required:
  `model_a.joblib`, `label_map.json`, `router_config.json`; optional: `metrics.json`,
  `model_b.onnx`. Missing optional is skipped (handles A-vs-B winner); missing required fails.

- [ ] **7b** Run `MLFLOW_TRACKING_URI=http://localhost:5001 uv run python
  scripts/pull_model_artifacts.py` (needs `mlflow` + `minio` healthy). Confirm
  `ml/intent/artifacts/` is populated.

- [ ] **7c** Author `ml/intent/artifacts/model_card.md` (task, A/B/C comparison from
  `metrics.json`, served file + SHA-256, threshold, limitations).

- [ ] **7d** Write `tests/eval/test_intent_gate.py`: rebuild the test split from
  `intent_dataset.csv` + `intent-split.json["test"]`, load `model_a.joblib`, assert
  macro-F1 ≥ min, routing accuracy-on-covered ≥ min at `router_config.fallback_threshold`,
  `label_map.json` order == generator `LABELS`, and the trivial guard. Add the `intent:` block
  to `eval_thresholds.yaml` (mins ~5 pts below the real scores).

- [ ] **7e** Wire `tests/eval` into `.github/workflows/ci.yml` so both intent and grad-risk
  gates run on every PR. Committed artifacts make CI hermetic (no MLflow access needed).

- [ ] **7f** `git add ml/intent/artifacts/` and commit — unblocks CI.

- [ ] **7g** model-server parity: add `intent_artifacts_dir` to
  `model-server/src/model_server/config.py` and `INTENT_ARTIFACTS_DIR` to the `model-server`
  service in `docker-compose.yml`. The shared `model-artifacts` volume + `model-artifacts-sync`
  service already carry both models once the pull script is generalized.

- [ ] **7h** Smoke: `docker compose up -d --build` → confirm `model-artifacts-sync` exits 0 with
  both `grad_risk/` and `intent/` populated; model-server healthy.

- [ ] **7i** Commit all changes and push to `002-phase-1-classifiers`.
