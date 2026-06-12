# SPEC — Intent Classifier & Router (Keel · the front door)

> **Rule:** This file is the source of truth. The generator, the notebook, the router, and the
> CI gate must match it. Any change here needs a `DECISIONS.md` entry.
>
> **Status:** This document is written *after* the model was built — it documents what shipped.
> It is the intent-classifier counterpart to `specs/003-phase-1-grad-risk-classifier/spec.md`.

---

## 1. Goal

Train **one** classifier that reads a student chat message and predicts its **intent label**.
The label is the router's decision: cheap, enumerable turns go straight to a deterministic
handler; only genuinely hard turns reach the bounded LLM agent.

This is the **first** of Keel's two trained models (second = graduation-risk). It is the front
door of the whole system: every inbound student message hits it first (after guardrails).

**Why it exists.** Most chat traffic is simple — lookups, status checks, plan management,
fully-specified actions. Sending every message to an LLM agent would be slow, expensive, and
unnecessary. The router keeps the LLM off cheap decisions and reserves its flexibility for the
turns that need it. A misroute is never dangerous: every action still passes the deterministic
engine check and the human approval gate before any write. *The classifier proposes a route;
deterministic systems and approval still protect every outcome* — the project's core principle
applied to routing itself.

## 2. What it predicts

- **Unit:** one student message (a short string).
- **Target:** one of **15** intent labels (see §4). One label = one handler.
- **Output:** the model returns a **full 15-class probability vector**. The serving code takes
  `argmax` for the label and `max` for the confidence. The **routing threshold is not inside
  the model** — it lives in `router_config.json` (§6).

The router's three-branch logic (documented, not part of the model):

1. If the session has a pending approval or an active flow → the deterministic state machine
   handles the turn (no classifier, no LLM).
2. Else if `confidence ≥ fallback_threshold` → run the single handler mapped to that label,
   directly.
3. Else (low confidence, ambiguous, or multi-intent) → hand to the bounded LLM agent, which
   picks among the same handlers as tools.

## 3. Changes from the capstone brief (log in `DECISIONS.md`)

| Brief said | We do | Why |
|---|---|---|
| Compare ML vs DL vs LLM | Compare **TF-IDF+LR (A) vs DistilBERT→ONNX (B) vs Gemini zero-shot (C)** | This is **text** data — the comparison the brief intended. A = classical ML, B = small DL exported to ONNX, C = LLM baseline (reference only, never registered/deployed). The grad-risk model later swaps this for a model-*family* comparison because it is tabular. |
| One router label per action | **15 labels** (added `my_info`, `chitchat` to the original 13) | Action-type requests get distinctive vocabulary (`waitlist`, `graduation`, `petition`, `switch my major`), so a small classifier separates them reliably. `my_info` and `chitchat` keep high-frequency trivial turns off the LLM entirely. |

Everything else from the brief stays (winner served lean from the model-server, no torch at
inference, macro-F1 gates CI).

## 4. Label contract (frozen)

`LABELS` is exactly these **15**, in this canonical order. It is the single source of truth for
the model output index, `label_map.json`, and the router's handler map. The order in
`scripts/generate_intent_dataset.py`, the notebook's `LABELS`, and `label_map.json` **must all
match**.

| # | label | meaning | distinct from |
|---|---|---|---|
| 1 | `plan` | build/figure out a schedule | `whatif` (hypothetical), `register` (enroll) |
| 2 | `whatif` | hypothetical "what happens if" | `plan`; "should I switch majors?" lives here |
| 3 | `advise` | open-ended advice/opinion ("is it wise?") | `petition` (formal exception) |
| 4 | `audit` | degree-audit lookup (what's left/done) | `predict` (risk), `my_info` (account facts) |
| 5 | `predict` | forward-looking risk/likelihood/on-track | `audit`; powered by the grad-risk model |
| 6 | `register` | actually enroll (a write) | `plan`, `waitlist` |
| 7 | `waitlist` | join/leave a waitlist (a write) | `register` |
| 8 | `plans_manage` | CRUD on saved Plan entities | `plan` (build), `register` (enroll) |
| 9 | `grad_apply` | file the graduation application (write F1) | — |
| 10 | `major_change` | officially change/declare major (write F2) | `whatif` "should I switch majors?" |
| 11 | `petition` | formal exception needing human approval (F3) | `advise` |
| 12 | `escalate` | hand off to a human advisor/registrar (F4) | — |
| 13 | `out_of_scope` | homework/cheating, off-topic, prompt-injection — refuse | `chitchat` |
| 14 | `my_info` | student's own account facts (GPA, ID, holds, standing) | `audit`, `predict` |
| 15 | `chitchat` | greetings, thanks, small talk, identity | `out_of_scope` |

**Two contexts, one label set.** Offline the labels are hand-assigned to seed phrases; online
the model emits an index into this same list. The list defines the contract for both.

## 5. How the dataset is made

No API calls — every example is hand-authored in `scripts/generate_intent_dataset.py`. Getting
the **label boundaries** right matters more than volume: adjacent intents (`whatif` vs
`major_change`, `advise` vs `petition`) are deliberately disambiguated with truthful labels.

- **Shape:** 15 labels × **70 examples** = **1,050 rows**.
- **Seeds → paraphrases:** ~12 seed ideas per label, each expanded into ~6 paraphrases. Voice is
  intentionally messy — typos, lowercase, slang, short/long forms, real course codes from
  `scripts/seed.py` (CS400 is intentionally absent → a natural prereq-waiver `petition` case).
- **All paraphrases of one seed share a `seed_group_id`.**
- **Dedup:** global exact-match + token-set Jaccard ≥ `0.92` near-duplicate removal (across
  labels too), then each label trimmed to exactly 70 (removing from the largest seed groups
  first, so all ~12 seeds stay represented).
- **Split:** grouped **by `seed_group_id`**, stratified by label, 80/20, seed 42. Because the
  split is by group, no seed (and none of its paraphrases) can appear on both sides —
  paraphrase leakage would inflate test scores. A hard assert guarantees the train/test group
  sets are disjoint.
- **Columns:** `text`, `label`, `seed_group_id`.

## 6. The three models + winner rule

All three are evaluated; only A or B can be the router. C is a reference baseline.

| Model | What | Registered? | Notes |
|---|---|---|---|
| **A — TF-IDF + Logistic Regression** | `FeatureUnion(word 1–2gram, char_wb 3–5gram)` → LR (`C=5.0`, `class_weight="balanced"`, multinomial) | yes (if winner) | lean, microsecond inference, **no torch**, small joblib |
| **B — DistilBERT → ONNX** | fine-tuned `distilbert-base-uncased`, exported to a single ONNX file; inference via `onnxruntime` (no torch at serve) | yes (if winner) | larger artifact, GPU to train |
| **C — Gemini zero-shot** | prompted LLM, no training | **never** | answers "is training worth it at all?" — reference only |

**Winner rule (A vs B only):** B wins **only if** its macro-F1 exceeds A's by **more than
0.005**; otherwise **A wins** (smaller artifact, zero GPU cost, no torch in the model-server).
**Current production = Model A.**

### Routing threshold (separate from the model)
The model returns the full probability vector. The **threshold lives in `router_config.json`**,
not in the model artifact — changing it needs no retraining and no re-registration. It is
chosen by sweeping thresholds and taking the lowest value that keeps accuracy-on-covered ≥ the
target (maximizing coverage while holding quality).

## 7. Evaluation + CI gate

Report for all three models. The gate checks the **production** model (currently A).

- **Main:** `macro_f1` (held-out test split).
- **Routing coverage:** at `router_config.fallback_threshold`, accuracy on the covered subset
  (messages with `max_prob ≥ threshold`) ≥ `covered_accuracy_min`.
- **Also report:** accuracy, per-message latency, confusion matrix.
- **Trivial guard:** `macro_f1 < macro_f1_trivial_guard_max` (data must not be trivially clean).
- **Contract check:** `label_map.json` label order == `LABELS` in the generator.

Thresholds live in `tests/eval/eval_thresholds.yaml` under `intent:` — set ~5 points below the
first clean run's real numbers, then commit.

```yaml
intent:
  macro_f1_min: <real − 0.05>
  covered_accuracy_min: <real − 0.05>
  macro_f1_trivial_guard_max: 0.99
```

## 8. Serving / routing contract (for later phases)

When the model-server actually loads this model (a later phase), it consumes:

| Artifact | Role |
|---|---|
| `model_a.joblib` (or `model_b.onnx` + `tokenizer_b/` if B wins) | the served classifier |
| `label_map.json` | `labels`, `label2id`, `id2label` — output index → label |
| `router_config.json` | `fallback_threshold`, target accuracy, winner, labels |
| `model_card.md` | task, comparison, **SHA-256** of the served file |

**Forward-pointer (not built here):** the model-server endpoint contract is
`message: str → {label: str, confidence: float, proba: float[15]}`; the router applies
`fallback_threshold` to decide direct-handler vs agent. The served file's SHA-256 must match
the model card, or the model-server refuses to boot (same rule as grad-risk). Define this
endpoint when serving lands.

## 9. Artifacts to produce

| Artifact | Path | Note |
|---|---|---|
| Full dataset | `data/intent_dataset.csv` | from the generator (1,050 rows) |
| Split | `data/intent-split.json` | grouped 80/20, written by the generator |
| Model (serving) | `ml/intent/artifacts/model_a.joblib` | `model_b.onnx` (+ `tokenizer_b/`) only if B wins |
| Label map | `ml/intent/artifacts/label_map.json` | names + label2id + id2label |
| Router config | `ml/intent/artifacts/router_config.json` | threshold + routing policy |
| Metrics | `ml/intent/artifacts/metrics.json` | winner metrics + comparison (the intent eval report) |
| Model card | `ml/intent/artifacts/model_card.md` | authored locally (notebook prints SHA-256s only) |

## 10. Done checklist

- [x] 15 labels frozen; generator, notebook, and `label_map.json` agree.
- [x] 70/label, 1,050 total; global dedup; grouped split with no leakage (asserted).
- [x] Three models trained and compared; winner rule applied; **A registered to Production**.
- [x] Threshold chosen and saved in `router_config.json` (outside the model).
- [x] Winner registered as `keel-intent-router @ production` in MLflow.
- [ ] Artifacts pulled from MLflow into `ml/intent/artifacts/` and committed (CI source of truth).
- [ ] CI gate green (macro-F1 + routing coverage + contract check).
- [ ] Served file SHA-256 matches the model card.

## 11. Non-goals

- No multi-label output (one intent per message; multi-intent turns fall to the agent).
- The LLM never owns routing — the agent is the fallback, not the main path.
- No drift monitoring / auto-retraining.
- Model C (Gemini) is never deployed as the router.
