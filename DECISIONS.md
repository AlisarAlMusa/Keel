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