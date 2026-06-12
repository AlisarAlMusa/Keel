# DATA.md — Datasets, Provenance, and Generative Assumptions

> Every dataset Keel trains or evaluates on is documented here: how it was made,
> what's real vs synthetic, how leakage is prevented, and how to regenerate it.
> Per CLAUDE.md §13 — never claim synthetic data is real.

---

## 1. Intent-classifier dataset (`data/intent_dataset.csv`)

The training set for the **router** — the trained intent model that sends easy
turns to the deterministic workflow and hard turns to the bounded agent
(CLAUDE.md §9, PLAN.md Phase 1/2).

### Provenance — hand-authored, no API calls

Every example was written by hand in
[training/intent/generate_intent_dataset.py](training/intent/generate_intent_dataset.py).
**No LLM or external API generated any text.** This is deliberate: the label
*boundaries* (e.g. exploratory "should I switch majors?" → `whatif` vs.
"officially switch my major" → `major_change`) are the hard part, and authoring
them by hand keeps the labels truthful and auditable. To regenerate:

```bash
uv run python training/intent/generate_intent_dataset.py
```

### Schema

| column          | type | meaning                                             |
| --------------- | ---- | --------------------------------------------------- |
| `text`          | str  | the student utterance (messy student voice)         |
| `label`         | str  | one of 15 intents (below)                           |
| `seed_group_id` | int  | the seed idea this paraphrase belongs to (global)   |

### Labels (15) and decision boundaries

| label          | what it is                                  | boundary note (nearest neighbor)                       |
| -------------- | ------------------------------------------- | ------------------------------------------------------ |
| `plan`         | build/figure out a schedule                 | not a hypothetical (`whatif`), not enrolling (`register`) |
| `whatif`       | hypothetical "what happens if"              | "should I switch majors?" lives here (exploratory)     |
| `advise`       | open advice/opinion ("is X wise/hard?")     | "is it wise to take CS400" → here; "can I take CS400 without the prereq" → `petition` |
| `audit`        | degree audit (what's remaining/done)        | not chances/risk (`predict`), not account facts (`my_info`) |
| `predict`      | graduation risk / on-track / chances        | powered by the grad-risk model, not an audit           |
| `register`     | actually enroll me (write action)           | not building a plan, not a full section (`waitlist`)   |
| `waitlist`     | join/leave a waitlist                       | separate write from `register`                         |
| `plans_manage` | CRUD on saved Plan entities                 | save/load/activate/compare/delete, not build           |
| `grad_apply`   | file the graduation application (F1)        | institutional write                                    |
| `major_change` | officially change/declare a major (F2)      | "officially switch" → here; "should I?" → `whatif`     |
| `petition`     | request an exception/override (F3)          | prereq waiver, credit overload, time-conflict override |
| `escalate`     | hand off to a human advisor (F4)            | no login/role creation, just a handoff                 |
| `out_of_scope` | homework/cheating, weather/chat, injection  | the router must refuse these                            |
| `my_info`      | the student's own account facts             | GPA, ID, holds, standing — lookups, not `audit`        |
| `chitchat`     | greetings, thanks, small talk, capability   | friendly, no academic intent                           |

### Construction

- **70 examples per label × 15 = 1,050.**
- ~12 **seed ideas** per label, each expanded into ~6 paraphrases. All
  paraphrases of one seed share a `seed_group_id`.
- Voice is intentionally messy: typos, lowercase, slang, short/long forms, real
  catalog course codes (`CS101`–`CS420`, `MATH101`–`MATH210` from
  [scripts/seed.py](scripts/seed.py)). `CS400` is intentionally absent from the
  catalog — a natural prereq-waiver `petition` case.
- `out_of_scope` includes homework/essay/cheating, weather/recipes/poems, and
  **3 prompt-injection seeds** (ignore-instructions, reveal-system-prompt,
  DAN/jailbreak).

### Dedup & determinism

- Exact-dup removal after normalization (lowercase, strip punctuation, collapse
  whitespace), **plus** a token-Jaccard ≥ 0.92 near-dup check (tuned so
  different course codes stay distinct examples).
- Generate 12×6 = 72 candidates/label, dedup, then trim to exactly 70 (dropping
  from the largest seed groups so all ~12 seeds stay represented).
- `random.seed(42)` fixes shuffle order and the split — fully reproducible.

### Train/test split (`data/split.json`) — leakage prevention

- **Grouped by `seed_group_id`, stratified by label, ~80/20.** No seed group
  (and therefore no paraphrase of it) appears on both sides — paraphrase leakage
  would inflate test scores (ENGINEERING_RULES §12).
- With 12 seeds/label, `max(2, round(0.2·12)) = 2` seeds/label go to test →
  **875 train / 175 test (~17%)**. (Grouped splits can't land exactly on 80/20;
  2/12 is closer to 20% than 3/12 would be.)
- The generator **asserts** the train and test group sets are disjoint.
- `split.json` carries row indices (`train`/`test`) and the `seed_group_id`
  lists per side so the guarantee is independently checkable.

### Known limitations

- English only (multilingual G2 is a cut-first feature).
- Single institution's course codes; generalization to other catalogs untested.
- Hand-authored → reflects the author's sense of each boundary; the intent-F1
  eval gate (PLAN.md Phase 2) is the check on that.

---

## 2. Graduation-risk dataset — *to be written (Phase 1, parallel track)*

Will be **synthetic** (allowed per CLAUDE.md §13). This section will document the
generative model, the class-imbalance ratio for the at-risk minority, the
features, and the assumptions — **before** any risk numbers are reported. Not yet
generated.
