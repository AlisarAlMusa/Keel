# Model Card — keel-intent-router

## Task
15-class intent classification of student chat messages. The label is the router's
decision: high-confidence turns go to a deterministic handler; low-confidence turns fall to
the bounded LLM agent. The model returns the full 15-class probability vector; the routing
threshold lives in `router_config.json`, not in the model.

## Data
Hand-authored by `scripts/generate_intent_dataset.py` — 15 labels × 70 examples = 1,050 rows.
~12 seed ideas per label, each expanded to ~6 paraphrases sharing a `seed_group_id`. Global
exact + Jaccard (≥ 0.92) dedup. The 80/20 split is **grouped by `seed_group_id`** so no
paraphrase of a training seed appears in test (leakage-free). Seed 42.
Train: 875 rows  |  Test: 175 rows.

## Three-model comparison (A vs B vs C)
| Model | what | macro_f1 | notes |
|---|---|---|---|
| **A — TF-IDF + LogisticRegression ← winner** | word (1–2) + char_wb (3–5) FeatureUnion → LR | **0.8034** | lean, ~8 ms/msg, no torch, 0.6 MB joblib |
| B — DistilBERT → ONNX | fine-tuned, exported to ONNX | — | larger artifact; only wins if margin > 0.005 |
| C — Gemini zero-shot | prompted LLM, no training | — | reference only; never registered/deployed |

**Winner rule:** B beats A only if its macro-F1 exceeds A's by more than 0.005; otherwise A
wins. **A is production.**

## Served artifact
File: `model_a.joblib`
SHA-256: `8708f944149c65955aca4c3da854c56eb571a17a2b12baf0603228029e645f62`

The model-server refuses to boot if the SHA-256 of the loaded file does not match.

## Labels (canonical order)
`plan, whatif, advise, audit, predict, register, waitlist, plans_manage, grad_apply,
major_change, petition, escalate, out_of_scope, my_info, chitchat`

## Routing policy
Threshold (`router_config.json` → `fallback_threshold`): **0.5115**. At this threshold the test
coverage is ~70% with accuracy-on-covered **0.926** (target 0.90). Change the threshold by
editing `router_config.json` — no retraining or re-registration needed.

## Serving note (important)
`predict()` returns the **string label** directly — use it. scikit-learn sorts string classes,
so `model.classes_` (and therefore the `predict_proba` column order) is **alphabetical**, which
is **not** `label_map.json`'s `id2label` order. To map a probability index to a label, use
`model.classes_[i]`, never `label_map.id2label[i]`.

## Limitations
- Trained on a synthetic, hand-authored dataset — it learns the authored intent boundaries, not
  organic student phrasing at scale.
- A misroute is non-dangerous by design: every action still passes the deterministic engine
  check and human approval gate before any write; ambiguous turns fall to the agent.
- No drift monitoring; retrain if the message distribution shifts.
- joblib is sklearn-version-sensitive (trained on 1.6.1). The CI gate and serving load it with a
  benign `InconsistentVersionWarning`; predictions are unaffected for TF-IDF + LR.
