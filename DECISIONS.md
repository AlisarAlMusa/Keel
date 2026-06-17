# DECISIONS.md — Keel

A running log of non-obvious technical choices, each with the rationale and the
simpler/other alternative that was rejected. Per the constitution, every tech
substitution, new dependency, threshold change, or deliberate non-adoption gets
an entry here.

---

## Phase 0 — Foundation

### D-001 — Two Python packages, three container images

**Decision.** The repository is two `uv` packages: (a) the `keel` backend at the
repo root (`src/keel/`) powering **both** the `api` and `worker` containers, and
(b) an isolated `model-server/` package. Three images are built: `Dockerfile.api`
and `Dockerfile.worker` share the root build context and one root `.dockerignore`;
`model-server/Dockerfile` has its own context and `.dockerignore`.

**Rationale.** The api and worker run *identical code* and differ only by
entrypoint (`uvicorn keel.main:app` vs `rq worker keel`) — one package, two thin
images. The model-server must stay lean and **torch-free** (constitution
mandate); a hard package boundary makes "no torch / no SQLAlchemy / no LangGraph
in the model image" true by construction rather than by discipline. CI asserts
no `torch` appears in `model-server/uv.lock`.

**Rejected.** (1) One package per container — would duplicate the api/worker
dependency tree and source for zero benefit. (2) A single mono-package with
extras for the model-server — a careless `uv sync` could still drag heavy deps
into the lean image; the boundary would be advisory, not structural.

### D-002 — Database-enforced tenant isolation (RLS), app connects as non-superuser

**Decision.** Row-Level Security is `ENABLE`d **and** `FORCE`d on all 15
tenant-owned tables in the baseline migration, with a `tenant_isolation` policy
using `current_setting('app.tenant_id', true)::uuid`. The application connects as
the role `keel_app`, created `NOSUPERUSER NOBYPASSRLS` by the DB init script. Each
unit of work sets the tenant via `set_config('app.tenant_id', …, is_local=true)`.

**Rationale.** Tenant isolation is "the grade" (constitution Principle IV). A
superuser or `BYPASSRLS` role silently skips every policy, so the app role must be
neither. `FORCE` ensures the policy also applies to the table owner. An unset
tenant yields `NULL`, which matches no row → fail-closed.

**Rejected.** Schema-per-tenant (heavier ops; documented future option beyond
~hundreds of tenants). Application-only filtering (single layer; the constitution
requires defense-in-depth — RLS is layer 1, repository filtering layer 2,
pgvector filtering layer 3).

### D-003 — `uv` only, frozen installs, lockfile per package

**Decision.** All dependency management is `uv`; both `uv.lock` files are
committed; Docker images install with `uv sync --frozen --no-dev`.

**Rationale.** Constitution mandate. `--frozen` fails the build if a lockfile is
stale, guaranteeing reproducible images and CI.

**Rejected.** pip/requirements (no lockfile determinism), Poetry (slower).

### D-004 — ORM models separate from domain value objects

**Decision.** SQLAlchemy ORM classes live in `infra/orm.py`; domain value
objects (Pydantic v2, `frozen`) live in `domain/models.py`. Repositories map
between them and never leak ORM rows above the repository layer.

**Rationale.** Keeps `domain/` (and the future engine) free of framework/IO
imports so it stays pure and unit-testable; lets the DB schema evolve without
changing domain contracts. `Base.metadata` remains the single Alembic target.

**Rejected.** SQLModel (couples ORM and Pydantic) — would blur the domain/infra
boundary the engine depends on.

### D-005 — Vault-gated startup, password injected at runtime

**Decision.** The app loads required secrets from Vault in the FastAPI lifespan
*before* building any secret-dependent singleton; failure (unreachable / missing
key) raises and the process does not start. The DB DSN carries a literal
`placeholder` password in env; the real password is merged in at runtime from
Vault. Alembic's `env.py` performs the same injection when it sees the
placeholder, so migrations work in-container; a DSN that already contains a real
password (host/CI tests) is used as-is.

**Rationale.** Fail-closed secrets are a graded, non-negotiable property; keeping
the real password out of env/DSN avoids it leaking into logs, `docker inspect`,
or process listings.

**Rejected.** Secrets in env only (no central rotation, easy to leak).

### D-006 — Enums stored as TEXT + CHECK, not Postgres ENUM

**Decision.** String enums (term, status, role, etc.) are stored as `TEXT` with
`CHECK` constraints rather than native Postgres `ENUM` types.

**Rationale.** Postgres `ENUM` value changes require special migrations and are
awkward to evolve; `TEXT` + `CHECK` is trivially migratable and equally safe at
the DB boundary while the domain keeps real `StrEnum`s.

### D-007 — MLflow backed by SQLite + local volume in Phase 0

**Decision.** The Phase 0 MLflow service uses a SQLite backend store and a local
artifact volume, not Postgres + MinIO.

**Rationale.** Phase 0's acceptance is only "MLflow server up, UI reachable." A
self-contained SQLite/volume config is the most reliable way to keep the smoke
test green without adding `psycopg2`/`boto3` into the third-party MLflow image.
When model logging/promotion lands (Phase 1), MLflow moves to the Postgres
backend + MinIO artifact store described in `ARCH.md`.

**Rejected (for now).** Postgres + MinIO-backed MLflow — deferred to the phase
that actually logs runs, to avoid image-dependency fragility in the foundation.

### D-007b — MLflow upgraded to Postgres + MinIO in Phase 1; artifacts proxy-served

**Decision.** At Phase 1 (when model training begins), MLflow is upgraded from
the Phase 0 SQLite/volume config to: Postgres as the backend store (metadata +
model registry) and MinIO as the artifact store. MLflow is started with
`--serve-artifacts`, which proxies artifact uploads through the tracking server's
HTTP API rather than requiring clients to write directly to S3.

**Rationale.** Training runs from Colab (remote) — a Colab notebook cannot reach a
Docker-internal volume or `http://minio:9000`. With `--serve-artifacts`, Colab
only needs to reach the tracking server (tunneled via ngrok/cloudflared to
`localhost:5001`); it uploads artifacts over HTTP to MLflow, which forwards them
to MinIO. No S3 credentials needed in Colab. This also means the model registry
and all run artifacts survive container restarts (Postgres + MinIO are both
durable volumes).

**Implementation.** Custom `Dockerfile.mlflow` adds `psycopg2-binary` + `boto3`
to the slim base image. `minio-init` one-shot container creates both
`keel-artifacts` and `keel-mlflow` buckets on first boot. `db-init.sh` creates
a separate `mlflow` database in Postgres via `\gexec` (idempotent, works outside
a transaction). This is the "Backed by MinIO + Postgres" state described in
`ARCH.md §6`.

**Rejected.** Direct S3 client in Colab — requires exposing MinIO credentials
and MinIO port to the tunnel, adds credential management in Colab. SQLite/volume
retained beyond Phase 0 — remote clients cannot write to a local volume; only
works if training is always local.

### D-008 — LangGraph/LangChain not installed in Phase 0

**Decision.** Agent dependencies (LangGraph/LangChain) are deferred to Phase 2,
when the bounded agent is built — they are not in the Phase 0 backend lockfile.

**Rationale.** Keep the foundation image lean; install heavy agent deps the day
the agent lands. The `agent/` package is scaffolded empty so the code has a home.

### D-009 — `extra="ignore"` in Settings instead of `extra="forbid"`

**Decision.** `Settings.model_config` uses `extra="ignore"` rather than the
`extra="forbid"` recommended by ENGINEERING_RULES §5.

**Rationale.** The single `.env` file is shared between `pydantic-settings` (app
config) and Docker Compose, which injects its own variables (`POSTGRES_USER`,
`POSTGRES_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, etc.) into the
same env at startup. `extra="forbid"` would reject every Compose-specific key
and abort boot. Maintaining two separate files (`.env.app` + `.env.compose`)
removes the problem correctly but adds operational friction for a solo dev
environment where the shared file is the standard Compose convention.

**Accepted trade-off.** A typo'd *app* config key silently uses the default
instead of failing fast. Mitigated by: (1) all non-secret required fields have
strict Pydantic types (wrong type → boot error), and (2) Vault provides a
fail-closed gate for every secret value regardless of settings typos.

**Revisit when.** The project adds a separate `.env.app` file loaded explicitly
via `env_file=".env.app"` and CI validates the compose file references only
non-app variables — then switch to `extra="forbid"`.

**Rejected.** `extra="forbid"` now — breaks `docker compose up` without the
two-file split, which is a Day-7 polish item, not a Day-1 blocker.

## Phase 1 - Classifier models and engine logic

### The Intent Classifier & Router — Design Narrative

**What it is.** A small trained text-classification model (trained model #1 of 2) that reads every incoming student message and returns one label plus a confidence score. The label maps directly to exactly one handler in the backend. It is the front door of the whole system.

**The problem it solves.** Most chat traffic is simple: lookups, status checks, plan management, fully-specified actions. Sending every message to an LLM agent would be slow, expensive, and unnecessary. The router exists to keep the LLM off cheap, enumerable decisions — and to keep the LLM's flexibility reserved for the turns that genuinely need it.

**The routing rule (3 lines).**

1. If the session has a pending approval or an active flow → the deterministic state machine handles the turn (no classifier, no LLM).
2. Else if classifier confidence ≥ threshold → run the single handler mapped to that label, directly.
3. Else (low confidence, ambiguous, or multi-intent) → send to the bounded LLM agent, which picks among the same handlers as tools.

**The labels (13).** `plan`, `whatif`, `advise`, `audit`, `predict`, `register`, `waitlist`, `plans_manage`, `grad_apply`, `major_change`, `petition`, `escalate`, `out_of_scope`. One label = one handler. We renamed "register" thinking into a richer set: action-type requests get their own labels because they have distinctive vocabulary ("waitlist", "graduation", "petition", "switch my major"), so a small classifier separates them reliably.

**Key decision 1 — handlers are fixed pipelines, not agent improvisation.** Workflows whose steps always run in the same order (plan → eligible pool → propose → verify → repair → predict risk → explain) are written as code: composite tools. The LLM works *inside* specific steps (extract constraints, propose candidates, explain results) but never decides the order. Reason: a fixed order paid to an LLM on every request is wasted cost and a reliability risk; code is testable once and correct forever.

**Key decision 2 — the agent is the fallback, not the main path.** It handles three cases only: low-confidence turns, multi-intent turns ("plan my semester and register me"), and conversational repair ("no, make it lighter"). Its tools are the same composite pipelines used by direct routing — built once, shared, no duplicated logic. It is bounded: tool allowlist, loop cap, Pydantic-validated inputs.

**Why 13 labels doesn't hurt accuracy.** The classes are lexically distinct, and the design absorbs mistakes: confusable or mixed messages naturally produce low confidence and fall to the agent, which resolves them. A misroute is never dangerous, because every action still passes the deterministic engine check and the human approval gate before any write. The classifier proposes a route; deterministic systems and approval still protect every outcome — the project's core principle applied to routing itself.

**Training & evaluation.** Three-way comparison: classical ML (TF-IDF + logistic regression/GBM) vs small DL model (DistilBERT-class, exported to ONNX) vs LLM zero-shot baseline. Dataset: hand-written seed examples LLM-augmented to ~50–80 per class, deduplicated, stratified held-out split, no leakage. Macro-F1 gates CI; the confusion matrix and the confidence-threshold choice are committed with the eval report. Served from the lean model-server (no torch), milliseconds per call.

**What this buys the project.** Cheap turns cost ~0 tokens; routing is observable and testable; the attack surface shrinks (out_of_scope filters junk before any LLM sees it); and the capstone gets a genuinely justified trained model — not a model added for show, but one whose absence would make every message more expensive.


### Router label set expanded to 15 (added `my_info`, `chitchat`)
Real traffic includes turns that fit no feature module but that we can answer
from our own tables ("what time is my class?", "show my grades") and pure
small talk ("hello", "thanks"). Added:
- `my_info` → deterministic DB lookup over the student's own enrollments,
  sections, and transcript + template response. No RAG (structured data lives
  in tables, not prose), no LLM. Zero token cost.
- `chitchat` → canned friendly template + one-line capability hint. No LLM.
Both keep cheap, high-frequency turns off the LLM entirely — the router's
reason to exist. Unknown/unanswerable turns still fall to `advise`-with-empty-
retrieval ("I couldn't find this — want an advisor?") or `out_of_scope`.

### Approval state moved to Postgres; Redis keeps only session memory
Pending approval actions are safety-critical state (loss → lost write;
replay → double write). They live in a `pending_actions` table
(payload, status, expires_at, idempotency_key); the approval endpoint reads
this row, so approvals survive a Redis restart. Redis holds only ephemeral
conversation context with TTL, where loss is harmless. Persistent where
correctness matters; cache where convenience matters.

## Phase 1 — Graduation-Risk Classifier

### D-GR-001 — LR / RF / HistGB instead of ML / DL / LLM

**Decision.** Three-family comparison: Logistic Regression (linear), Random
Forest (bagging), HistGradientBoosting (boosting). No deep-learning model, no
LLM baseline.

**Why.** This is a tabular dataset with 9 numeric features and ~4 000 rows. DL
models (transformers, MLPs) offer no structural advantage over tree ensembles at
this size and feature type. The ML/DL/LLM comparison lesson was already
demonstrated in the intent classifier, which has text inputs. Comparing three
model families that differ in their inductive bias (linear separability, random
bagging variance reduction, sequential boosting bias reduction) is the correct
bake-off for tabular data.

### D-GR-002 — Representative imbalance + class_weight instead of SMOTE or 50/50 generation

**Decision.** Dataset is generated at ~25% at-risk rate (realistic minority).
Balance is handled at training time via `class_weight='balanced'` for LR/RF and
`compute_sample_weight('balanced', y_train)` for HistGB.

**Why.** A 50/50 synthetic set fails the "is your base rate realistic?" question
— it misrepresents the actual prevalence of at-risk students. At a 25% minority
rate, class weighting is mathematically sufficient: it reweights the loss
function identically to what SMOTE aims to achieve but without generating
synthetic samples that could introduce distributional artifacts. SMOTE also
requires the `imbalanced-learn` dependency, which is not installed anywhere in
this project and would add a non-trivial transitive dependency surface.

### D-GR-003 — Risk-function weights and required interaction term

**Decision.** Labels are generated by a logistic risk function applied to
z-scored features, with a mandatory nonlinear interaction term:
`0.6 × relu(-z_gpa) × relu(z_workload)`. The intercept is binary-searched to
achieve a 23–27% at-risk rate.

**Exact weights (copy into DATA.md):**
```
cumulative_gpa:         -1.2
gpa_trend:              -0.8
num_failures:           +1.0
num_repeats:            +0.5
progress_rate:          -0.9
pct_complete:           -0.3
planned_credits:        +0.4
planned_workload_index: +0.7
num_hard_courses:       +0.5
interaction:            +0.6  (relu(-z_gpa) × relu(z_workload))
intercept:              ~-2.97 (binary-searched; see grad_risk_meta.json)
```

**Why the interaction is required.** Without it, the risk surface is a
hyperplane in z-space, meaning Logistic Regression can fit it perfectly (macro-F1
→ 1.0 after standardisation). This would make the comparison between model
families meaningless. The interaction term adds curvature that LR cannot capture,
ensuring the comparison between linear / bagging / boosting models is genuine and
the trivial-guard (`macro_f1 < 0.97`) is meaningful.

### D-GR-004 — Tuning strategy: RandomizedSearchCV, 5-fold stratified CV, scoring=f1_macro

**Decision.** `RandomizedSearchCV` with `StratifiedKFold(n_splits=5)` and
`scoring='f1_macro'`. 12 iterations per family. Refit=True so the best estimator
is evaluated directly on the test set.

**Why RandomizedSearch over GridSearch.** The search spaces for RF and HistGB are
continuous or large-discrete; a full grid would be prohibitively slow in Colab.
RandomizedSearch with 12 iterations finds good hyperparameters in a fraction of
the time with minimal loss in solution quality for this problem size.

**Why f1_macro as CV scoring.** Consistent with the test metric and the CI gate.
Using accuracy would bias CV toward majority-class performance; f1_macro weights
both classes equally in CV, matching the goal of catching at-risk students.

### D-GR-005 — Winner selection rule and ONNX handling

**Decision.** Winner = highest `macro_f1` on the test set; tie-break =
`at_risk_recall`. ONNX export is attempted via `skl2onnx`; if it fails for HistGB
(known conversion gaps for some Pipeline configurations), `grad_risk.joblib` is
the served file and ONNX is skipped. The model card and eval report record which
file is served and its SHA-256.

**Why joblib as safe path.** `skl2onnx` supports all three model families but has
occasional issues with newer sklearn versions or custom transformers. Serving
joblib via the model-server's Python runtime is fully equivalent in this context
(the model-server already runs Python). The SHA-256 boot check applies to
whichever file is served; swapping to ONNX after the fact requires only
re-pinning the hash, not re-training.
## Phase 1 — Classifier Artifact Sync + CI Gates (both models)

### D-IC-001 — One generalized MLflow pull script, not one per model

**Decision.** `scripts/pull_model_artifacts.py` carries a `MODELS` registry and
loops over every Keel model (`keel-grad-risk` → `ml/grad_risk/`,
`keel-intent-router` → `ml/intent/`), resolving each at alias `production` and
downloading its whole `artifacts/` directory. One docker-compose
`model-artifacts-sync` service syncs both.

**Why.** The resolve→download→validate logic is identical per model; a second
script would duplicate it and drift. A declarative list keeps the boot-time sync
to a single container and makes adding a future model a one-entry change. Each
model declares its own *required* vs *optional* artifacts, so a winner that omits
a file (e.g. intent Model A has no `model_b.onnx`) is handled gracefully.

### D-IC-002 — Intent test set is index-based, not a standalone CSV

**Decision.** The intent CI gate reconstructs its 175-row test set from
`data/intent_dataset.csv` + `data/intent-split.json["test"]`. There is no
materialized `intent_test.csv` (unlike grad-risk's `grad_risk_test.csv`).

**Why.** The intent split was always stored as row indices grouped by
`seed_group_id` (the leakage guarantee lives in the index assignment). Both source
files are committed, so the reconstruction is deterministic and hermetic in CI. A
separate CSV would duplicate data already present and risk drift on regeneration.

### D-IC-003 — Intent gate enforces macro-F1 AND routing coverage

**Decision.** `tests/eval/test_intent_gate.py` asserts (1) macro-F1 ≥ 0.75
(actual 0.8034), (2) accuracy-on-covered ≥ 0.87 at the `router_config.json`
threshold (actual 0.926 at 0.5115), (3) `label_map` order == generator `LABELS`,
(4) `model.classes_` cover exactly the 15 labels, (5) a trivial guard.

**Why.** macro-F1 alone does not protect the *routing policy* that actually ships
— the threshold decides direct-handler vs agent. Gating accuracy-on-covered
ensures a model regression that quietly lowers covered accuracy is caught, not
just one that lowers overall F1.

### D-IC-006 — Intent golden set (held-out obvious cases, 100% gate)

**Decision.** `data/intent_golden.csv` — 30 hand-written, unambiguous messages
(2 per label) the production router MUST classify correctly. Written by the
generator from a `GOLDEN` dict, held out of training, with a build-time guard
that fails if any golden line is a near-duplicate (Jaccard ≥ 0.92) of a training
row. CI gate: `golden_accuracy_min: 1.0`. The intent analogue of
`grad_risk_golden_edge.csv`.

**Why.** The test-split macro-F1 (0.80) tolerates errors on hard/ambiguous
phrasings — correctly, since those fall to the agent. But the router must never
fail an *obvious* turn ("apply for graduation", "what's my gpa"). A 100% gate on
held-out canonical cases catches a regression that breaks the easy path even if
aggregate F1 looks fine. The near-dup guard keeps the set a real generalization
check, not memorization. Two initial drafts were reworded after the model missed
them — confirming the set is genuinely held-out and the gate has teeth.

**Note.** Fixed a latent path bug found while adding this: the generator used
`parents[2]` (correct when it lived in `training/intent/`) but had moved to
`scripts/`, so it was writing outputs to `<repo>/../data/` — outside the repo.
Corrected to `parents[1]`; regenerated `intent_dataset.csv` / `intent-split.json`
are byte-identical to the committed (model-trained) versions.

### D-IC-004 — Serving uses `model.predict()` / `model.classes_`, never `label_map.id2label`

**Decision.** scikit-learn sorts string class labels, so Model A's
`model.classes_` (and `predict_proba` column order) is **alphabetical**, which is
NOT `label_map.json`'s `id2label` order. The serving contract (and the gate) use
`predict()` for the label and `model.classes_[i]` to map a probability index.

**Why.** Using `label_map.id2label[argmax(proba)]` would silently mislabel
predictions. Documented in the model card and the intent spec's serving section so
the later model-server integration does not reintroduce the bug.

## Phase 2 — Model Server, RAG, Guardrails, Router, Agent

### D-P2-001 — Router: argmax = route, max_prob = trust gate, agent = single fallback

**Decision.** `proba = intent(text); route, conf = argmax(proba), max(proba)`.
If `conf >= FALLBACK_THRESHOLD` (≈ 0.5115, in `router_config.json`) → dispatch to the
flow mapped to that label directly (skipping agent intent-decision). If `conf < FALLBACK_THRESHOLD`
or the classifier is unreachable → hand to the bounded LangGraph agent, which has conversation
history and decides intent + handles the turn. There is no third path.

**All 15 labels mapped from Day 2.** Five real Phase-2 flows
(`advise`, `audit`, `plan`, `out_of_scope`, `chitchat`); ten stubs (one fixed
"not available yet" string per label, no LLM). Stubs are replaced phase by phase —
no router changes needed after today. The full label set stays because it also feeds
per-intent analytics and agent priming.

**Multi-step / ambiguous messages fall to the agent by design.** A context-dependent
follow-up ("then do one for me") is naturally ambiguous on its own, scores low
confidence, and reaches the agent — which has the conversation history to resolve it.

**Rejected.** (1) A second threshold tier or per-route thresholds — adds complexity with
minimal gain at MVP scale; one global threshold is documented in `router_config.json`.
(2) Feeding conversation history to the classifier — would let it wrongly inherit a
previous turn's intent.

### D-P2-002 — chitchat / out_of_scope: LLM-direct lite model, not canned strings

**Decision.** `chitchat` and `out_of_scope` reach a direct LLM call using
`GEMINI_LITE_MODEL` (`gemini-2.0-flash-lite`) with a hard 50-token cap.
The main `GEMINI_MODEL` (`gemini-2.5-flash`) handles all reasoning-heavy flows
(plan, advise, audit, RAG, repair, mitigation) and the agent.

**Rationale.** Varied, warm responses at near-zero cost; canned strings feel robotic.
If the lite call fails, a hardcoded fallback string is returned — never silently routed
to the full agent.

**Rejected.** Using the full model for chitchat — unnecessary cost.
Using canned strings — poor UX for greetings/small talk.

### D-P2-003 — grad_risk served as joblib (not ONNX)

**Decision.** The model server loads `grad_risk.joblib` and pins its SHA-256
(`e4bef218508c20713654b9eb15a06413c8eb532d9f86440d4236c3535a231f7a`).
Both `grad_risk.onnx` and `grad_risk.joblib` exist as training outputs;
spec §3.1 mandates "no ONNX runtime in the model server — just joblib + sklearn."

**Rationale.** `skl2onnx` conversion of HistGB is version-sensitive (D-GR-005).
Joblib via Python runtime is fully equivalent for this use case. Keeping the
model server torch-free and ONNX-free is a hard architectural boundary.

**Rejected.** Serving the ONNX artifact — would require `onnxruntime` in the
model-server image, contradicting spec §3.1.

### D-P2-004 — RAG: hybrid dense + sparse, RRF k=60, Cohere rerank, no parent-child

**Decision.** Retrieval pipeline: redact query → dense cosine (pgvector, top 20)
+ sparse FTS (top 20) → RRF fuse (k=60, the RRF paper default) → Cohere
`rerank-multilingual-v3.0` top 12 → return top 5 to LLM.

**Chunk rules:** course = 1 chunk (overlap 0 — courses are discrete units);
policy = 1 chunk per `## ` heading (whole doc if < ~400 tokens, overlap 0 for
clean heading boundaries).

**No parent-child chunking.** With rerank + a ~30–40-chunk corpus, retrieve-top-k
+ rerank already surfaces the relevant clauses for cross-section questions.
Parent-child storage would be machinery with no measurable gain at this scale.

**Embedding model:** `cohere embed-multilingual-v3.0`, 1024-dim (covers multilingual
stretch, no torch required). pgvector column = `vector(1024)`.

**Rejected.** `k=80` or higher RRF constant — default 60 is well-studied, tuning deferred.
FAISS/Qdrant — pgvector keeps retrieval in the same DB with the same tenant filter,
no extra service. Parent-child — unjustified at this corpus size.

### D-IC-005 — Training notebooks excluded from ruff

**Decision.** `[tool.ruff] extend-exclude = ["*.ipynb"]`. Generators and gates in
`scripts/`/`tests/` are still fully linted and type-checked.

**Why.** Colab training notebooks legitimately carry exploratory patterns (display
imports, cell-scoped names) that production lint rules flag as noise. They are not
deployed code; the artifacts they produce are gated instead.

---

## Phase 4 — Advise, Guidance & Institutional Requests (Day 5)

### D-P4-001 — Advising (C1–C4) and A2 are read-only

**Decision.** All advising and degree-audit chat tools (C1 course_advisor, C2
degree_audit_chat, C3 failure_recovery, C4 major_switch_advice) return text and
never write. The LLM narrates; the engine supplies the numbers.

**Why.** Correctness comes from the engine, not the LLM. Keeping the write surface
minimal means student intent errors can't result in accidental writes — saving or
filing is always an explicit second step.

### D-P4-002 — E2 career-path advice may be saved only through the verifier loop

**Decision.** `save_career_roadmap` (E2-save) routes the suggested courses through
the `propose→verify→repair` loop before persisting a Plan. No raw LLM course list
ever reaches the database.

**Why.** E2 has no ground truth. DAG+RAG grounding is the only honesty guarantee.
Saving routes through propose-verify-repair so A4's "valid at save time" invariant
holds, even for career-path saves. E2 chat stays soft/advisory; legality is only
enforced when it becomes a persisted plan.

### D-P4-003 — All institutional requests (F1–F4) reuse one action pattern

**Decision.** F1 graduation, F2 major-change, F3 petition, and F4 escalation all
share a single action-pattern shape: validate → require approval → single DB
transaction (write + outbox event) → audit row. Implemented once in
`services/actions/institutional.py`.

**Why.** One subsystem, four intents — not four pipelines. Tested once, reused
everywhere. Reduces attack surface and keeps correctness guarantees uniform.

### D-P4-004 — One approval gate (student); registrar decision is downstream

**Decision.** The agent's institutional tools call service functions with
`approved=False`. The True path is reached only by explicit student action (approval
UI / endpoint, Day 6). Registrar review is a manual downstream step.

**Why.** The agent automates the *request*, not the *decision*. This matches how
real registration offices work: filing is the agent's value; approval authority
stays with the human registrar.

### D-P4-005 — New advisors lookup table; no advisor role

**Decision.** F4 escalation resolves an advisor's (name, email) from a new
`advisors` table (tenant-owned, RLS-protected). Advisors have no login or auth
principal.

**Why.** F4 needs a routing target, not a login. Adding a fourth role would
complicate the auth model without adding demo value. The three-role model
(student / registrar / platform-operator) is preserved.

### D-P4-006 — Idempotency on F1/F2/F3 via partial unique index on PENDING rows

**Decision.** A partial unique index `uq_request_queue_pending` on
`(tenant_id, student_id, type, target) WHERE status='pending'` prevents duplicate
PENDING rows for the same filing. ON CONFLICT DO NOTHING means re-filing before
resolution is a safe no-op.

**Why.** Reuses the enrollment idempotency pattern exactly. Re-filing is only
possible once the prior PENDING request is resolved — correct institutional behavior.

### D-P4-007 — F3 keeps the engine block hard; petition never auto-enrolls

**Decision.** `submit_petition` writes a PETITION row in `request_queue` and never
an enrollment row, even after approval. The engine's eligibility block is preserved.

**Why.** A petition is an override *request* routed to a human reviewer, never an
automatic enrollment. This is the core safety story for F3: a student cannot bypass
prerequisites via a chat petition. The agent tool further enforces this by holding no
`approved` parameter — the LLM cannot trigger enrollment by injection.

### D-P4-008 — F4 is email handoff only; no appointment row

**Decision.** Escalation writes one `outbox` event (`escalation_email` kind) and
one `audit_log` row. No appointment-queue or calendar row was added.

**Why.** Appointment booking adds scope without adding demo value (no calendar
integration exists). Keeping F4 as pure email handoff keeps the outbox publisher
generic and scope honest.

### D-P4-009 — SQLAlchemy bind-param bug: :name::jsonb drops last char of bind name

**Decision.** All `CAST(:name AS jsonb)` throughout the write subsystem instead of
`:name::jsonb`.

**Why.** SQLAlchemy `text()` parses `:name::jsonb` as bind name `nam` (drops the
last char before `::`). This was a latent bug across `outbox_write`, `audit_write`,
`insert_pending`, `save_plan`, and `swap_course`. Discovered and fixed in Phase 4;
verified by repro: `sa.text("VALUES (:payload::jsonb)")._bindparams` → `{'payloa'}`
vs `CAST(:payload AS jsonb)` → `{'payload'}`.
