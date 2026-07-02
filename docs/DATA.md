# DATA.md — Datasets, Provenance, and Generative Assumptions

> Every dataset Keel trains or evaluates on is documented here: how it was made,
> what's real vs. synthetic, how leakage is prevented, and how to regenerate it.
> Per CLAUDE.md §13 — never claim synthetic data is real.

Keel has three datasets: the **intent-classifier** training set (hand-authored), the
**graduation-risk** training set (synthetic, documented), and the **RAG corpus** (mock
catalog/policy prose). Model results and SHA-256 pins live in the model cards under
`ml/*/artifacts/model_card.md`; eval gates are in [`EVALS.md`](EVALS.md).

---

## 1. Intent-classifier dataset (`data/intent_dataset.csv`)

The training set for the **router** — the trained intent model that sends easy turns to
a deterministic workflow and hard turns to the bounded agent (CLAUDE.md §9).

### Provenance — hand-authored, no API calls

Every example was written by hand in
[`scripts/generate_intent_dataset.py`](../scripts/generate_intent_dataset.py).
**No LLM or external API generated any text.** This is deliberate: the label *boundaries*
(e.g. exploratory "should I switch majors?" → `whatif` vs. "officially switch my major"
→ `major_change`) are the hard part, and authoring them by hand keeps the labels truthful
and auditable. Regenerate with:

```bash
uv run python -m scripts.generate_intent_dataset
```

### Schema

| column | type | meaning |
| --- | --- | --- |
| `text` | str | the student utterance (messy student voice) |
| `label` | str | one of 15 intents (below) |
| `seed_group_id` | int | the seed idea this paraphrase belongs to (global) |

### Labels (15) and decision boundaries

| label | what it is | boundary note (nearest neighbor) |
| --- | --- | --- |
| `plan` | build/figure out a schedule | not a hypothetical (`whatif`), not enrolling (`register`) |
| `whatif` | hypothetical "what happens if" | "should I switch majors?" lives here (exploratory) |
| `advise` | open advice/opinion ("is X wise/hard?") | "is it wise to take CS400" → here; "can I take CS400 without the prereq" → `petition` |
| `audit` | degree audit (what's remaining/done) | not chances/risk (`predict`), not account facts (`my_info`) |
| `predict` | graduation risk / on-track / chances | powered by the grad-risk model, not an audit |
| `register` | actually enroll me (write action) | not building a plan, not a full section (`waitlist`) |
| `waitlist` | join/leave a waitlist | separate write from `register` |
| `plans_manage` | CRUD on saved Plan entities | save/load/activate/compare/delete, not build |
| `grad_apply` | file the graduation application (F1) | institutional write |
| `major_change` | officially change/declare a major (F2) | "officially switch" → here; "should I?" → `whatif` |
| `petition` | request an exception/override (F3) | prereq waiver, credit overload, time-conflict override |
| `escalate` | hand off to a human advisor (F4) | no login/role creation, just a handoff |
| `out_of_scope` | homework/cheating, weather/chat, injection | the router must refuse these |
| `my_info` | the student's own account facts | GPA, ID, holds, standing — lookups, not `audit` |
| `chitchat` | greetings, thanks, small talk, capability | friendly, no academic intent |

### Construction

- **70 examples per label × 15 = 1,050.** ~12 **seed ideas** per label, each expanded into
  ~6 paraphrases; all paraphrases of one seed share a `seed_group_id`.
- Voice is intentionally messy: typos, lowercase, slang, short/long forms, real catalog
  course codes (`CS101`–`CS420`, `MATH101`–`MATH210` from
  [`scripts/seed.py`](../scripts/seed.py)). `CS400` is intentionally absent from the
  catalog — a natural prereq-waiver `petition` case.
- `out_of_scope` includes homework/essay/cheating, weather/recipes/poems, and **3
  prompt-injection seeds** (ignore-instructions, reveal-system-prompt, DAN/jailbreak).

### Dedup, determinism, and leakage prevention

- Exact-dup removal after normalization, **plus** a token-Jaccard ≥ 0.92 near-dup check
  (tuned so different course codes stay distinct examples). `random.seed(42)` fixes the
  shuffle and split — fully reproducible.
- **Train/test split** (`data/intent-split.json`) is **grouped by `seed_group_id`,
  stratified by label, ~80/20.** No seed group (and therefore no paraphrase of it) appears
  on both sides — paraphrase leakage would inflate test scores. With 12 seeds/label, 2
  seeds/label go to test → **875 train / 175 test (~17%)**. The split is stored as row
  indices plus the per-side `seed_group_id` lists, so the disjointness guarantee is
  independently checkable; the generator also asserts it. (The intent gate reconstructs
  its test set from these two files — there is no materialized `intent_test.csv`; see
  DECISIONS D-IC-002.)
- **Golden set** (`data/intent_golden.csv`): 30 hand-written, unambiguous messages (2 per
  label) held out of training, guarded against near-duplication of any training row. The
  router must classify all 30 correctly (CI gate `golden_accuracy_min: 1.0`; see
  DECISIONS D-IC-006).

### Known limitations

- English-authored (multilingual replies are handled by a system-prompt rule at inference,
  not in this dataset).
- Single institution's course codes; generalization to other catalogs is untested.
- Hand-authored → reflects the author's sense of each boundary; the intent-F1 gate is the
  check on that.

---

## 1b. Seeded catalog, sections & instructors (`scripts/seed.py`)

The structured catalog, sections, and instructor names are **mock/seeded** demo data
standing in for the SIS ([`OVERVIEW.md`](OVERVIEW.md) §6–§7) — not a real institution's
records.

- Two tenants (Northane, Summit), each with ~24 courses, prerequisite chains, program
  requirements, sections, and student transcripts.
- Each course is seeded with **two sections per offered term** (year 2026): a primary and
  an alternate (some at 8 AM or on Fridays, some intentionally full). This gives the
  agentic section-selection step real options to reason over against a student's stated
  preferences ("no 8am, no Fridays") — the LLM proposes a fitting combination and the
  engine validates it (DECISIONS D-P6-002; the flow is folded into
  [`../specs/008-phase-5-frontend-widget-auth/spec.md`](../specs/008-phase-5-frontend-widget-auth/spec.md)).
- **Instructor names are synthetic** (`_INSTRUCTORS` in `scripts/seed.py`, e.g. "Dr.
  Haddad") — invented for the demo, not real people. They carry no signal and are
  display-only.
- A few sections are seeded **full** (`CS301` at Northane, `DS210` at Summit) so the
  "section full → offer waitlist / another term" path is exercised end-to-end.

---

## 2. Graduation-risk dataset (`data/grad_risk.csv`)

The training set for the **graduation-risk model** — the second trained model, which scores
a *valid* plan on-track / at-risk and feeds an LLM mitigation explanation (D1 in
[`OVERVIEW.md`](OVERVIEW.md)).

### Provenance — synthetic, generated from a documented risk function

Generated by [`scripts/generate_grad_risk_data.py`](../scripts/generate_grad_risk_data.py)
(`random.seed(42)`, 4,000 rows). Synthetic is **allowed and appropriate** here: real
transcript data is FERPA-protected, which is the legitimate reason to synthesize. The data
carries a deliberate, documented signal plus noise — never "purely random." Regenerate with:

```bash
uv run python -m scripts.generate_grad_risk_data
```

### Schema — 9 numeric features + label

`cumulative_gpa, gpa_trend, num_failures, num_repeats, progress_rate, pct_complete,
planned_credits, planned_workload_index, num_hard_courses` → `at_risk ∈ {0, 1}`.

All features are engine-computed at inference from the shared feature module
(`domain/features/grad_risk.py`) — the model never sees names, IDs, or emails. Feature #8
(`planned_workload_index`) is the same difficulty × credits aggregation the deterministic
workload signal uses (one computation, two consumers).

### Generative model (the assumptions, stated plainly)

Labels are **sampled** from `Bernoulli(sigmoid(risk-logit))` — not thresholded — over
z-scored features, using a logistic risk function with a **mandatory nonlinear interaction
term** (all weights in `data/grad_risk_meta.json`, mirrored in DECISIONS D-GR-003):

```
risk_logit =  -1.2·z_gpa  -0.8·z_gpa_trend  +1.0·z_failures  +0.5·z_repeats
              -0.9·z_progress  -0.3·z_pct_complete  +0.4·z_planned_credits
              +0.7·z_workload  +0.5·z_hard_courses
              +0.6·relu(-z_gpa)·relu(z_workload)          # required interaction
              -2.96875                                     # intercept (binary-searched)
```

- **Why the interaction is required.** Without it the risk surface is a hyperplane in
  z-space, which Logistic Regression fits perfectly (macro-F1 → 1.0) — making the
  three-family comparison meaningless. The interaction adds curvature LR cannot capture, so
  the linear / bagging / boosting bake-off is genuine and the trivial-guard has teeth.
- **Class balance.** The intercept is binary-searched to a realistic **~24.85% at-risk
  minority** (`at_risk_rate` in the meta). A 50/50 synthetic set would misrepresent the base
  rate; instead, imbalance is handled at *training* time with class weighting, not SMOTE or
  oversampling (DECISIONS D-GR-002).

### Splits and evaluation

- **Train 3,200 / test 800** (stratified, `random.seed(42)`); the held-out test set is
  materialized at `data/grad_risk_test.csv`.
- **Edge-case golden set** (`data/grad_risk_golden_edge.csv`): hand-constructed boundary
  students the model must classify correctly (CI `edge_case_accuracy_min: 1.0`).
- Metrics reported per class (macro-F1 **and** at-risk recall, the minority class), never
  just aggregate accuracy. Winner and three-family table: `ml/grad_risk/artifacts/model_card.md`.

### Honesty note (the framing that survives a defense)

The model card states plainly: **this model learns the generator's notion of risk, not
validated real-world risk.** It is the "weak supervision, documented honestly" lesson
applied directly. Never present it as predicting real graduation outcomes.

---

## 3. RAG corpus (`data/rag-corpus/`)

The advising RAG prose — **Keel-owned**, registrar-uploaded in production, seeded for the
demo. Four mock documents, two per tenant:

```
data/rag-corpus/
  northane_catalog.md   northane_policies.md
  summit_catalog.md     summit_policies.md
```

- **Course descriptions** are chunked one-per-course (overlap 0 — courses are discrete
  units); **policy docs** are chunked one chunk per `## ` heading. Embedded with Cohere
  `embed-multilingual-v3.0` (1024-dim) into pgvector, tenant-tagged (DECISIONS D-P2-004).
- The prose is what the **LLM explains from**; prerequisite *facts* in any answer are
  grounded against the deterministic DAG, never taken from the prose. This is the honesty
  guarantee for advising — the model cannot invent a prerequisite.
- The corpus is distinct from the structured SIS catalog (the DB tables the engine queries).
  See [`ARCHITECTURE.md`](ARCHITECTURE.md) §6 — "anything computed on → DB table; anything
  explained from → vector store."
