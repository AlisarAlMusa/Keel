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

### D-007 — MLflow backed by Postgres + MinIO, artifacts proxy-served

**Decision.** MLflow runs with **Postgres as the backend store** (metadata + model
registry) and **MinIO as the artifact store**, started with `--serve-artifacts` so
artifact uploads proxy through the tracking server's HTTP API rather than clients
writing directly to S3. Custom `Dockerfile.mlflow` adds `psycopg2-binary` + `boto3`;
`minio-init` creates the `keel-artifacts` and `keel-mlflow` buckets; `db-init.sh`
creates a separate `mlflow` database via `\gexec`. This is the "Backed by MinIO +
Postgres" state in `ARCHITECTURE.md §6`.

**Rationale.** Training runs from Colab (remote) — a notebook cannot reach a
Docker-internal volume or `http://minio:9000`. With `--serve-artifacts`, Colab only
needs the tracking server (tunneled via ngrok/cloudflared to `localhost:5001`) and
uploads artifacts over HTTP; no S3 credentials in Colab. The registry and all run
artifacts survive container restarts (both are durable volumes).

**Phase-0 note.** The foundation phase (before any run was logged) started on a
self-contained SQLite backend + local volume purely to keep the smoke test green
without adding `psycopg2`/`boto3` to the third-party image; it was upgraded to the
above the moment model training landed. The SQLite config never shipped a real run.

**Rejected.** Direct S3 client in Colab — requires exposing MinIO credentials/port to
the tunnel. Keeping SQLite/volume — remote clients cannot write to a local volume.

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

**Decision.** The agent automates the *request*, not the *decision*: an institutional tool
only ever **stages** a pending action; the actual filing happens after explicit student
approval, and registrar review is a further downstream step. **As-built**, "explicit student
approval" is the staged-action state machine (`POST /actions/{id}/approve`), realized in
D-R-003 — no institutional tool carries an `approved` field, so neither the agent nor an
injection can self-file. (A brief regression once had the petition tool filing with
`approved=True`; D-R-003 closed it.)

**Why.** Filing is the agent's value; approval authority stays with the human — matching how
real registration offices work.

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


## Phase 5 — Frontend, Service Architecture, Auth Boundaries

### D-P5-000 — Boundary summary (what Keel is vs. what the SIS is)

These are the foundational truths that all Phase 5 decisions follow from:

- **Keel is an AI layer over the SIS, not a SIS.** System of record = SIS. Keel adds planning, advising, prediction, and safe registration on top.
- **Two systems, one API contract.** Keel and the SIS are separate (separate DBs in production), linked by a per-tenant adapter — never a shared database. Only Keel's own surfaces (admin + widget) share Keel's DB.
- **No catalog/student CRUD in Keel.** Structured SIS data is seeded in the demo; registrar-managed in the SIS in production. Keel admin manages only Keel-owned config + RAG.
- **Keel admin feeds RAG prose only** (`catalog.md`, `policy.md`). Structured rows (courses/prereqs/sections/students/rules incl. credit caps, holds, windows) are SIS-domain → seeded → read by the engine.
- **Adapter is documented now, implemented after the demo.** The engine reads SIS data through a repository boundary; a real SIS adapter is a post-demo swap. Refactoring it pre-demo is risk without reward.
- **Auth: the university authenticates the student.** The portal backend vouches via a server-side token mint; Keel never trusts a client-supplied `student_id`. Token = widget-only. Lazy mint on chat open.
- **Mock SIS portal, two roles** (student + registrar), both reading SIS-domain tables directly. Pages are read-only views of real seed data with dead write buttons; the request queue is the one functional registrar action.
- **Request workflow is SIS-domain.** The agent drafts + submits; the registrar approves in the (mock) SIS portal. This dissolves the "request status" question — everyone reads it as plain SIS data.
- **Provenance via audit, not SIS schema changes.** "via Keel" comes from Keel's audit log + the SIS's existing transaction-source field; mocked with a `source` column.
- **Manual Drop cut.** Write-proof = the widget enrolling and the read-only schedule updating.
- **One Postgres in the demo**, two logical domains (SIS-domain + Keel-domain), tenant-isolated by RLS. No physical split.

---

### D-P5-001 — Mock SIS portal is a standalone microservice (Node/Express + React)

**Decision.** The mock SIS portal runs as its own Docker container: a Node.js service
(Express backend + Vite-built React SPA) entirely separate from `keel-api`. It exposes
`/api/*` portal routes and serves the portal SPA at `/`. Its only call into Keel is the
widget token mint (`POST /internal/mint-token`) — a single server-to-server request
protected by `portal_service_secret`.

**Why a separate container.** The portal IS the SIS, not Keel. In production a
university's SIS portal is a completely different system — different team, different
codebase, different deployment. The container boundary makes that separation concrete and
defensible. If the portal shared the Keel API container, the demo would imply Keel owns
the SIS portal, which contradicts the core architecture claim.

**Why Node/Express, not a second FastAPI.** The portal has no AI, no async ML inference,
no pgvector. Its workload is: serve static files + small SQL reads + one outbound HTTP
call. Node/Express is the minimal runtime for this; adding a second Python/uvicorn stack
would be over-engineering with no payoff. A real university SIS portal is typically a
Java/Rails/Node app, not a Python AI service — Node aligns with that reality.

**Rejected.** (1) Portal routes inside `keel-api` — blurs the SIS/Keel boundary; makes
the demo argument harder to defend. (2) Portal frontend as its own third container
(separate from the portal backend) — see D-P5-002.

---

### D-P5-002 — Portal frontend and portal backend are co-located in one container

**Decision.** The portal container runs Express, which both serves the Vite-built React
SPA (as static files) and handles the portal API routes (`/api/portal/*`). There is no
separate container or CDN for the portal React app.

**Why they belong together.**

1. **Session cookie security.** The portal session cookie is `HttpOnly` and set by the
   Express backend. If the SPA were served from a different origin, the browser would
   block the cookie on all cross-origin requests — making the session unreadable. Same
   container = same origin = no `SameSite`/CORS cookie problems.
2. **This is how a monolithic SIS works.** A university's SIS portal is one deployed
   application that serves its own pages and handles its own auth. We are mocking a
   monolith, not a microservices SIS frontend/backend split — the mock should match the
   shape of the real thing.
3. **No benefit from splitting.** The only reason to separate a frontend container is to
   serve it via CDN or to have independent scaling. Neither applies in a
   single-tenant demo environment. An extra container would add an nginx reverse proxy,
   CORS configuration, and two deployment artifacts for zero architectural gain.

**Rejected.** Separate frontend container for the portal — adds serving complexity and
CORS surface area with no benefit at demo scale.

---

### D-P5-003 — Widget iframe and Admin-UI served from keel-api; widget.js runs on the portal page

**Decision.** The Vite-built `widget/dist/` and `admin/dist/` are mounted as
`StaticFiles` on `keel-api` at `/widget/` and `/admin-ui/`. `widget.js` is also served
from `keel-api` at `/widget.js`. No additional container for either frontend.

**The token flow (the key mechanism):**
`widget.js` is served from keel-api but is **loaded via a `<script>` tag on the portal
page** — it therefore runs inside the portal page's JavaScript execution context.
Because of this:

1. `widget.js` uses `window.location.origin` (the portal's origin) to call
   `GET /api/portal/keel-token` — a **same-origin request from the portal page**. The
   session cookie is included automatically. No cross-origin cookie complexity.
2. Portal Express backend verifies the session, calls `POST /internal/mint-token` on
   keel-api (server-to-server with `portal_service_secret`), and returns the JWT.
3. `widget.js` opens the widget iframe at `keel-api/widget/` and **postMessages the
   token** to the iframe (with an origin check). The iframe stores it in memory.
4. The widget iframe (on keel-api origin) calls `POST /chat` at the same origin — no
   CORS needed for API calls.

This is exactly how Intercom, Stripe, and Zendesk embeddable widgets work: the
provider's script runs on the host page's origin, uses the host's session to authenticate,
and opens an iframe at the provider's origin for the actual product surface.

**Why widget iframe stays on keel-api origin.** Same-origin between the iframe and
`/chat` means the Bearer token only needs an `Authorization` header — no credential
cookies, no complex CORS. The widget token in memory is inaccessible to the portal page
(cross-origin iframe isolation) and disappears when the iframe is torn down.

**Why admin-ui stays in keel-api.** The admin console only calls `/admin/*` on keel-api.
Serving it elsewhere adds a container boundary where there is no domain boundary.

**Rejected.** Serving the widget iframe from the portal container — would make `/chat`
cross-origin for the iframe, requiring `Authorization` header CORS plus a more complex
token-passing story. Serving admin-ui in a separate container — just static files, no
reason for a process boundary.

---

### D-P5-004 — npm workspaces monorepo for all three frontends

**Decision.** `frontend/` is an npm workspaces root. Four packages live under it:
`ui/` (shared primitives), `widget/`, `admin/`, and `portal/`. One `npm install` at
the root installs all four; `react`, `typescript`, `vite`, and shared UI deps are
hoisted to the root `node_modules` and deduplicated.

**Why a monorepo.** All three frontends use the same design token set, the same
component primitives (`Badge`, `Button`, `Card`, `Table`, `StreamingText`), and the same
TypeScript/Vite setup. Without workspaces, each app would carry its own copy of these
deps (~150 MB × 3). The monorepo makes sharing the `ui/` library trivial (`import { Badge }
from '@keel/ui'`) with zero duplication.

**Why not three completely independent `package.json` files.** Each app can independently
vary its version of any dep by adding it as a local override — workspaces don't prevent
that. But common deps (react, vite, typescript, the design system) are declared once,
locked once, and updated once. This is standard practice (Turborepo, Nx, pnpm workspaces)
and the right call for three apps that share a design system.

**Rejected.** Three independent npm projects — 3× install time, 3× lockfile drift risk,
shared components would need copy-pasting or a private npm registry.

---

### D-P5-005 — Service-to-service auth: portal_service_secret (HS256 Bearer JWT)

**Decision.** The portal Express backend calls `POST /internal/mint-token` on keel-api
to get a widget JWT for the authenticated student. This call carries a Bearer JWT signed
with `portal_service_secret` (from Vault). keel-api verifies the service JWT before
minting. The browser never sees `portal_service_secret`; the exchange is entirely
server-to-server.

**Why a dedicated shared secret, not the widget_token_secret.** `widget_token_secret`
signs tokens that arrive from browsers — a different trust level. Using the same secret
for service calls and widget tokens would mean a compromised widget token could impersonate
the portal service. Separate secrets = separate trust domains = breach containment.

**Why a JWT over a plain shared API key.** A JWT can carry a short `exp` (e.g., 60 s)
so a replayed service token has a narrow attack window. A plain shared API key is
permanent until rotated. Both are from Vault; the JWT adds a time-bound property for
free.

**Rejected.** mTLS between portal and keel-api — correct for production with a service
mesh; over-engineered for a capstone demo. The shared secret + JWT exp provides
adequate security for a demo network where both containers share a Docker Compose
network.

---

### D-P5-006 — Widget token: lazy-minted by widget.js on the portal page, postMessaged to iframe, memory-only

**Decision.** Launcher button loads on page load (no token). On click, `widget.js`
(running on the portal page) fetches the token from the portal backend (same-origin,
session cookie included), opens the iframe, and **postMessages** the token to the iframe
with an explicit origin check. Inside the iframe, the token lives in a closed-over
JavaScript variable. It is never written to localStorage, sessionStorage, or a cookie.

**Why postMessage rather than URL query param.** Passing the token in the iframe `src`
URL (`?token=…`) puts it in the browser's address bar and history, and in server logs.
postMessage with an origin check (`event.origin === KEEL_API_ORIGIN`) is the
browser-native inter-frame messaging channel — invisible outside the two frames,
dropped if the origin doesn't match.

**Why lazy.** Minting at page load attaches a live 15-min credential to every page view.
Lazy mint means the credential exists only while chat is open — minimum exposure window.

**Why memory-only.** localStorage survives tab close and is readable by any same-origin
script (XSS pivot). sessionStorage is also script-readable. A variable inside the iframe
is cross-origin-isolated — the portal page cannot read it — and disappears when the
iframe is torn down or the tab is closed.

**The iframe's origin check.** The widget iframe only accepts postMessage events from
`KEEL_API_ORIGIN` (the keel-api URL passed via the `data-keel-url` attribute on the
`<script>` tag). A rogue frame on another origin cannot inject a token.

**Rejected.** URL param — in browser history and server logs. localStorage — XSS risk,
survives session. Cookie from keel-api to the iframe — would require `SameSite=None;
Secure` and a more complex setup for cross-origin cookie use.

---

## Phase 5 Addendum — Platform Operator, Admin/Operator Auth, Second Tenant Portal

### D-A-001 — Third role restored: platform operator

**Decision.** `platform_operator` is added to the `users` table role set. The operator identity carries **no `tenant_id`** (column is nullable from migration 0006), enforced by two DB check constraints: `ck_operator_no_tenant` and `ck_admin_has_tenant`. Operator endpoints live under `/platform/*` and are guarded by `require_role("platform_operator")`.

**Rationale.** Without the operator, there is no way to provision / suspend / erase tenants short of direct DB access. The operator is a "controlled doorway, not god mode" — it touches only platform-domain tables and aggregate functions, never tenant content.

**Rejected.** A superuser/god-mode role that can read all tenant data — structural isolation invariant would be gone. A separate admin microservice — unnecessary complexity for a demo.

---

### D-A-002 — Auth = email + password for admin and operator

**Decision.** Both `tenant_admin` and `platform_operator` authenticate via `POST /auth/login` with email + bcrypt password. The issued JWT carries `{ sub, role, iat, exp }` plus `tenant_id` for admins (omitted for operators). The admin login applies `assert_tenant_active` — a suspended tenant's admin cannot log into Keel.

**Rationale.** A tenant-id-only auth (the previous X-Admin-Token approach) gives no per-person accountability and is semi-public (it ships in widget snippets). Email + password is the standard web auth pattern and maps cleanly to the existing `users` table.

**Rejected.** Keeping X-Admin-Token — not a real auth mechanism, fails the "per-person accountability" requirement. fastapi-users — full framework for our two-role use case is over-engineering; a single endpoint using bcrypt + PyJWT is sufficient.

---

### D-A-003 — Portal login is real email + password, portal-domain only

**Decision.** `POST /portal/login` accepts `{ email, password }`. A portal instance knows its
own tenant (`PORTAL_TENANT`), so it resolves that tenant and looks up `portal_user` by email
under **RLS** inside `withTenantTx`, verifies bcrypt, and sets the session cookie — a foreign
email simply returns zero rows → generic 401. Keel's `users` table is untouched (portal-domain
auth only). *(The pre-session lookup was originally a `portal_find_by_email()` SECURITY DEFINER
function that bypassed RLS; that turned out to be unnecessary — the portal always knew its
tenant — so it was replaced with the RLS-scoped query above and the function was dropped in
migration 0012. See D-R-015 for the final mechanism.)*

**No suspend check at `/portal/login`.** Suspension darkens Keel (the AI layer), not the university's SIS. Students still log into the portal and see My Schedule even when Keel is suspended. The suspend gate fires at `/portal/keel-token` (widget token mint) and `/chat`.

**Rejected.** Checking suspend at login — Keel has no authority over the SIS portal. Storing portal passwords in Keel's `users` table — blurs the SIS/Keel domain boundary that is the core architecture claim.

---

### D-A-004 — Second tenant portal = one image, two compose services

**Decision.** `portal-northane` (:3001) and `portal-summit` (:3002) are two compose services from the **same Dockerfile**, differentiated only by env (`PORTAL_TENANT`, `PORT`, `VITE_UNIVERSITY_NAME`, `VITE_UNIVERSITY_INITIAL`). Each tenant's `widget_config.allowed_origins` is seeded with its portal origin (`http://localhost:3001` / `:3002`).

**Rationale.** Two origins make the Keel origin-check demonstrable across tenants — a Northane token replayed on Summit's origin is rejected. One codebase means Summit "follows Northane" by construction; no forked components.

**Rejected.** Forking the portal frontend per tenant — code drift, double maintenance. A single portal serving both tenants — two origins cannot be demonstrated.

---

### D-A-005 — Erase is async, confirmation-gated, idempotent; platform_audit survives

**Decision.** `POST /platform/tenants/{id}/erase` validates `confirm_name == tenant.name`, writes a `platform_audit('erase', requested=True)` row, then enqueues `erase_tenant_job` via RQ. The worker cascade-deletes every row carrying `tenant_id` (including `portal_user` rows for that tenant), then deletes MinIO objects prefixed by the tenant_id, then deletes the tenant row. `platform_audit` uses `ON DELETE SET NULL` on the FK so audit rows survive the tenant deletion. Rerun on an already-erased tenant is a no-op.

**Rejected.** Synchronous erase in the request path — too slow and blocks the response for large tenants. Silent erase without confirmation — unrecoverable mistake if tenant name is mistyped.

---
## Post-integration hardening

Decisions taken during a security/correctness pass after the frontend/backend
integration. Each entry states the decision that shipped (not a to-do); `DESIGN.md`
is the as-built record.

### D-R-001 — `/actions/*` authenticate with the verified widget JWT (not headers)

**Decision.** The approve/reject endpoints now derive identity from the verified
widget Bearer JWT (`get_widget_context` + `verify_origin_or_403`) — the same
dependency `/chat` uses. The prior `X-Student-Id`/`X-Tenant-Id` header scheme is
removed. **Why.** Those headers were spoofable (anyone could approve any action by
setting them) and the widget never sent them, so approval always 401'd — the demo
spine was broken at the contract layer. **Rejected.** Keeping the placeholder
headers "until later" — it is both a critical auth hole and a broken contract.

### D-R-002 — Identity + thread_id are bound from the verified context, never the LLM

**Decision.** `run_agent` binds the verified `tenant_id`/`student_id` and the real
graph `thread_id` into a contextvar; write/stage tools call `resolve_identity` /
`resolve_thread_id`, which override any LLM-emitted values. **Why.** Tools opened a
tenant session from an LLM-supplied `tenant_id` (a cross-tenant vector under
injection), and the LLM-supplied `thread_id` was garbage (it echoed the student_id),
so the approval resume targeted a non-existent checkpoint and the write silently
never executed. **Rejected.** LangGraph `InjectedState` across every tool — correct
but high-churn; the contextvar override is behavior-preserving and lower-risk. Read
tools still rely on RLS + per-query filtering (residual, documented in DESIGN §10).

### D-R-003 — All writes (incl. institutional F1–F4) go through the gated action pattern

**Decision.** The petition tool no longer calls its service with `approved=True`;
all four institutional tools now *stage* a pending action (migration 0010 extends
`ck_actions_type`) and execute only on an approved resume, exactly like enrollment.
**Why.** The petition path let the agent (or an injection) file a request with no
approval, contradicting the injection-safety property; F1/F2/F4 had no wired
execution path at all. Now every filing is gated and reaches the registrar queue
only after approval.

### D-R-004 — Execution-time re-validation + capacity locking

**Decision.** `execute_enrollment_tx` and `fulfill_waitlist_tx` re-check the
registration hold and re-check capacity under `SELECT … FOR UPDATE` before inserting,
and stamp `source='keel'`. **Why.** The prior code inserted unconditionally and only
the counter UPDATE was capacity-gated → overbooking + counter drift; holds placed
during the approval window were ignored; and Keel writes weren't marked "via Keel".

### D-R-005 — Workers enumerate tenants then scan under RLS; scheduler thread added

**Decision.** Every worker job lists active tenants from the non-RLS `tenants` table
and processes each inside a `tenant_session`; the worker entrypoint runs a scheduler
thread that enqueues the recurring jobs. **Why.** `keel_app` is NOBYPASSRLS, so the
old unscoped scans of `outbox`/`sections`/`actions` matched `tenant_id = NULL` and
returned zero rows — the entire outbox/notify/waitlist/expiry tier was silently dead
— and nothing enqueued the periodic jobs.

### D-R-006 — Repository layer made real and used by the write/ledger path

**Decision.** `repositories/core.py` adds tenant-scoped `LedgerRepository` /
`ActionsRepository` (asserting row tenant); `audit_write`/`outbox_write` and the
action insert/expire delegate to them (zero caller churn). **Why.** The repository
layer (defense-in-depth layer 2, and the production SIS-seam boundary) existed only
as an unused base class. Read paths already filter `tenant_id` on every query under
RLS; the write surface now also passes through the repository boundary.

### D-R-007 — Stale-migration / contract fixes

**Decision.** (a) Migration 0009 `widget_config_all()` returns the 4 columns the app
queries (it had dropped `persona`). (b) The portal's `outbox` insert includes the
NOT-NULL `kind` column (it was rolling back the registrar decision tx). (c)
`usage_event.kind` is `'llm'` (the constraint forbids `'agent'`/`'classifier'`, so
cost rows were silently failing). (d) `mint-token` verifies `student ∈ tenant`
(per-portal secrets remain a recommended hardening). **Why.** Code/migration drift
from prior fix-ups; each silently broke a feature (persona prompt, registrar
notifications, cost view, cross-tenant mint).

## Second hardening pass (auth + DEFINER surface + tracing)

### D-R-008 — `keel_definer` role restores cross-tenant SECURITY DEFINER functions

**Decision.** A dedicated `keel_definer` role (`NOLOGIN BYPASSRLS`, created by the
superuser in `scripts/db-init.sh`, granted to `keel_app` as membership) owns every
genuinely cross-tenant SECURITY DEFINER function that must read *before* a tenant
session exists: `keel_find_user_by_email` (Keel-console login), the operator
aggregates (`platform_*`), and the startup bootstrap reads (`widget_*`,
`tenant_names_all`). Migration 0011 reassigns them and grants `SELECT`; `db-init.sh`
adds `ALTER DEFAULT PRIVILEGES … GRANT SELECT … TO keel_definer` for future tables.
*(The `portal_*` lookups were briefly in this set too, but the portal always knows its
own tenant, so they were later replaced with RLS-scoped queries and dropped in
migration 0012 — see D-R-015. The list above is the final BYPASSRLS surface.)*
**Why.** A prior ownership change set these to
`keel_app` (`NOBYPASSRLS`), so they ran *under* RLS with no tenant context and
returned only `tenant_id IS NULL` rows — every tenant_admin/student/portal login
silently 401'd; only the operator could log in. `keel_app` stays `NOBYPASSRLS`
(isolation intact); only these vetted functions bypass, and via a no-login role,
not the `postgres` superuser. **Rejected.** Granting `keel_app` BYPASSRLS (destroys
the isolation guarantee) and owning app functions by `postgres` (a far larger
privilege than needed). Also repaired in the same pass: all 17 auth rows shared one
constant bcrypt hash (a stale seeding artifact matching no documented password) —
reset in place to the documented demo passwords (the current seed already hashes
per-user, so fresh builds are correct).

### D-R-009 — Remaining Medium/Low audit items

**Decision.** **M-4:** `db_with_tenant` now binds `app.tenant_id` inside an explicit
`session.begin()` so the `SET LOCAL` shares one transaction with the handler's
queries (mirrors `tenant_session`) — previously it could drift and make RLS fail
closed. **L-2:** dropped the unused `authorization` param from `require_role`.
**L-3:** narrowed best-effort `except Exception` to the operational error
(`SQLAlchemyError` for usage accounting, `RedisError`/`ValueError`/`TypeError` for
the Redis cache) so genuine bugs surface; the deliberate request-protection
boundaries (`execute_node`, `run_agent`) stay broad-but-error-logged. **L-4:** no
action — migration 0006 already drops/recreates `ck_users_role` with all four roles
(the audit flagged 0001 in isolation). **M-5/M-6:** unchanged by decision — the
state-machine approval (SPEC §8 annotated) and the single `/keel` role-based console.

### D-R-010 — Read tools bind the verified identity (G1 / H-2 closed)

**Decision.** All 17 read/advisory tools (`advising`, `guidance`, `planning`) now
call `resolve_identity` / `resolve_tenant` at entry, overriding any LLM-supplied
`tenant_id`/`student_id` with the verified context — matching the write tools.
**Why.** Tools opened `tenant_session(UUID(tenant_id))` from an LLM argument, so a
prompt injection could scope a read to another tenant. The H-2 residual (write path
only) is now closed on the read path too. Test-safe: the resolver falls back to the
arguments when no identity is bound (direct unit-test calls).

### D-R-011 — Per-portal service secrets (G2)

**Decision.** Each portal presents its own `portal_service_secret_<slug>` (Vault);
the API maps `{tenant_id: secret}` at startup and `mint-token` lets a secret mint
**only its own tenant's** tokens (cross-tenant → 403). The shared
`portal_service_secret` stays as a legacy fallback (authorizes any tenant). **Why.**
A single shared secret meant a leak at one university could attempt to mint another
tenant's tokens; per-portal secrets + the existing `student ∈ tenant` check make
that impossible. Verified: Northane secret → Northane 200, → Summit 403, bogus 401.

### D-R-012 — Structured plan cards surfaced from the agent (G3)

**Decision.** `propose_plan` emits engine-verified, risk/workload-scored candidates
as structured `PlanData` through a per-turn mutable-list channel
(`agent/plan_channel.py`); `run_agent` → `RouterResponse` → `ChatResponse.plans`
carry them to the widget (which already renders `PlanCard`/`PlanTabsCard`). **Why.**
The structured candidates never reached the response, so the widget only showed the
model's prose. A mutable container (not a ContextVar value) is used because the flow
is child→parent and LangGraph may run nodes under a copied context. Verified: `/chat`
returns 3 cards with codes/credits/risk/workload.

### D-R-013 — Pluggable email transport; ON in simulation, Keel-actions only (G4)

**Decision.** `infra/email.py` provides `LoggingEmailSender` (simulation — logs the
send, no real mail) and `SMTPEmailSender` (real `smtplib`), selected by
`get_email_sender` from `keel_smtp_*`. Email is **ON by default**
(`keel_email_enabled=True`); with SMTP disabled the send is simulated. Every Keel
email is addressed to a single demo inbox (`keel_email_simulate_to`,
`mousaelisar@gmail.com`) since there are no real per-student mailboxes. **Only
Keel-originated events email** — an explicit allowlist (`_KEEL_EMAIL_EVENTS`:
enrollment/waitlist/seat/petition/graduation/major-change/escalation). SIS-domain
events the portal writes to the outbox (`request.approved` / `request.rejected`,
i.e. a registrar decision) are skipped: that outcome is the university's to
communicate, not Keel's. **Why.** `_send_email` was a hard-coded log stub; this makes
real delivery a config change (set `keel_smtp_enabled=true` + host), keeps the demo
safe (no real mail), and ensures Keel never claims credit for an SIS action. The
outbox remains the delivery guarantee; a transport failure propagates so RQ retries.
Verified: Keel actions → simulated send to the demo inbox; `request.*` → skipped.

### D-R-014 — End-to-end tracing: Jaeger + OTel auto-instrumentation + agent spans

**Decision.** Make the existing OTel scaffolding actually emit traces, into a
single Jaeger UI:
- **Backend:** an all-in-one **Jaeger** service in compose (UI `:16686`, OTLP gRPC
  `:4317`); `OTEL_EXPORTER_OTLP_ENDPOINT` defaults to `http://jaeger:4317` so traces
  flow with no `.env` change (empty still disables cleanly).
- **Auto-instrumentation** (`infra/tracing.instrument_libraries`): SQLAlchemy (engine
  queries — the deterministic engine's reads + repos), Redis (session/cache), and
  **httpx** (the outbound LLM/Gemini, Cohere rerank, and model-server calls — this is
  what makes the "LLM" step visible with timing, no LLM-specific SDK needed). Wired
  in the API lifespan once the engine/Redis singletons exist.
- **Agent spans** (`agent/tracing.py`, kept out of `infra/` because it imports
  LangChain types): a parent `agent.turn` span, an `agent.llm` span per step
  (records the model's tool-call decisions + response preview), and per-tool
  `agent.tool.<name>` spans (input args + output preview) via best-effort
  `model_copy` wrapping of each tool's coroutine.
- **Worker:** same `configure_tracing` + `instrument_libraries` in its entrypoint, so
  outbox publish + notification delivery land in the same Jaeger UI as `keel-worker`.

A chat turn now reads top-down: FastAPI request → `agent.turn` → `agent.llm` →
`agent.tool.*` → SQLAlchemy/Redis/httpx child spans, each with inputs/outputs.

**Why.** The scaffolding existed (`configure_tracing`, FastAPI instrumentation) but
`OTEL_EXPORTER_OTLP_ENDPOINT` was empty and there was no backend, so spans went
nowhere; only FastAPI was instrumented (no DB/cache/LLM visibility) and the agent
emitted no spans at all — exactly the "watch each step with its input/output" need.
All instrumentation is best-effort and never blocks boot; previews are capped
(≤500 chars) and pass through `redact()` at egress so a span never carries a full
transcript or PII. **Rejected (for now).** OpenLLMetry/`traceloop-sdk` and LangSmith
— richer LLM-specific capture but a heavier dep / separate SaaS pane; deferred to
STRETCH. The httpx spans already give per-call LLM timing in the same Jaeger UI.

### D-R-015 — Portal lookups are RLS-scoped, not BYPASSRLS (shrink the DEFINER surface)

**Decision.** The portal server (`frontend/portal/server/index.cjs`) no longer calls
the `portal_*` SECURITY DEFINER functions. A portal instance always knows its own
tenant (`PORTAL_TENANT`), and the `tenants` table has no RLS, so it now resolves its
tenant_id (`resolvePortalTenantId`) and runs ordinary **RLS-scoped** queries inside
`withTenantTx(tenantId, …)`:
- **Login** resolves the tenant *first*, then looks up `portal_user` by email under
  that tenant's RLS. A cross-portal email returns 0 rows → generic 401. The old
  explicit "account does not belong to this portal" 403 is **gone — the tenant match
  is now structural** (RLS), and 401 leaks less (no portal-membership enumeration).
- **Student list** reads `students ⋈ users ⋈ tenants` RLS-scoped, replacing
  `portal_list_students()` which fetched **all tenants** then filtered in JS.

Migration **0012** then drops the three now-unused functions (`portal_find_by_email`,
`portal_find_student` — already dead, never called — `portal_list_students`). **Why.**
These bypassed RLS for a "pre-session" lookup that was never actually pre-session: the
portal knew its tenant all along, so the bypass was unnecessary surface. After this,
the only BYPASSRLS DEFINER functions left (D-R-008) are the genuinely cross-tenant
ones: `keel_find_user_by_email` (Keel-console login — one host serves operator + both
tenants' admins, so the tenant is unknown until the user is found), the operator
aggregates (`platform_count_*`, `platform_usage_summary` — cross-tenant by role,
aggregate-only), and the startup bootstrap reads (`tenant_names_all`,
`widget_config_all`, `widget_origins_all`). **Rejected.** Detecting the tenant from
the portal *Host* header — the per-tenant service secret (D-R-011) is a strictly
stronger, non-spoofable signal we already have. (Completes the portal half of the
STRETCH "shrink the BYPASSRLS surface" item.)

---

## Phase 6 demo-polish decisions

### D-P6-001 — Re-registering a term overrides the prior registration (replace, don't reject)

**Decision.** When an approved enrollment is executed for a term the student already
has a registration in, the new plan **replaces** the prior one rather than being
rejected or layered on top. In `execute_enrollment_tx` (the single write path used by
both the agent and the portal button), after the new sections are secured we drop —
in the **same transaction** — every prior `enrolled` row for the same `(term, year)`
that is **not** part of the incoming registration (overlapping sections are kept, so
there is no churn and no needless email). Each drop sets `status='dropped'` and
decrements `sections.enrolled`. The audit row records `dropped_section_ids` alongside
the new `enrollment_ids`.

**Why.** A demo student re-running the planning loop for the same term would otherwise
either hit a confusing "you already have a registration" wall or silently double-book.
"Override" keeps the flow smooth and matches the mental model that *the approved plan
is your registration for that term.*

**Safety ordering.** The drop is deliberately performed **after** the new sections are
enrolled and is **gated on `secured_count > 0`**. If every incoming section turned out
full at execution time, nothing is dropped — a failed enrollment never leaves the
student with a dropped prior plan and nothing in its place. Because the whole thing
runs inside one `tenant_session` transaction, a mid-write error rolls back both the
inserts and the drops together. A prior section that reappears in the new plan is
reactivated (`dropped → enrolled`) rather than re-inserted, because the unique
`(tenant_id, idempotency_key)` constraint forbids a second insert for the same
`(student, section)`.

**Deferred to a real add/drop implementation (future work — NOT in the demo).** This
override is intentionally blunt. A production registrar flow should additionally:
(1) **check the add/drop calendar** — silent replacement is only acceptable inside the
open registration / add-drop window; outside it the drop must become a petition;
(2) **alert the student** with an explicit "this will drop X, Y from your current
schedule — confirm?" diff before writing, not just an after-the-fact note;
(3) honour **withdrawal deadlines and `W` grade rules** (a late drop is a withdrawal,
not a clean delete, and affects the transcript);
(4) respect **tuition/financial-aid credit thresholds** (dropping below full-time can
have billing and aid consequences);
(5) keep **waitlist side-effects** consistent (dropping a seat should trigger the
waitlist worker for that section). These are listed here so the simplification is on
the record, not silently assumed.

### D-P6-002 — Agentic, preference-aware section selection (registration-section-flow)

**Decision.** Registration section choice is **agentic, not portal-style filtering**.
The student states preferences in natural language ("no 8am, no Fridays"); the engine
returns the open-section pool and the agent/engine pick a fitting, conflict-free,
**open** section per course. Concretely:

- **The LLM picks sections, the engine verifies (mirrors the plan propose→verify loop).**
  `propose_sections` (read-only; realises `SPEC.md §7`'s `search_sections`) returns each
  open section per course with its `section_id`, day/time, instructor, seats, and whether
  it meets the prefs. The LLM reasons over them and picks the best section per course, then
  calls `stage_enrollment(section_ids=[...])`. `_verify_chosen_sections` re-verifies each
  chosen id: exists, belongs to a requested course (an injected id for an unrelated course
  is rejected), is open, and the set is conflict-free — invalid → `ToolError` → the LLM
  repairs. The LLM may choose sections precisely because the engine re-verifies every one
  before staging (the engine still owns legality and the final write target).
- **Fallback ("you pick for me"):** `stage_enrollment` with no `section_ids` runs
  `_resolve_sections_for_courses`, which ranks pref-meeting sections first (greedy,
  conflict-free) — the engine picks. Either path returns the chosen day/time + instructor;
  the staged approval message shows the schedule and flags any pref-violating pick.
- `propose_plan` now flags courses with **no open section** in the target term (verify()
  checks prereqs/credits/offering, not live seats), so a plan is honest about
  registrability — keep + flag, never silently drop.
- Seed: two sections per course per offered term with synthetic instructors and varied
  times (some 8am/Friday/full) so preferences discriminate and the
  "full → waitlist / another term" branch is exercised (migration `0013_section_instructor`).

**Why.** This is the project's core principle ("intelligence proposes, the engine
verifies") applied to sections: the LLM does the fuzzy preference-matching; the engine
owns legality (open + conflict-free) and still resolves the write target. The student
approves a card showing the chosen schedule — they never hand-pick a section UUID.

**Safety.** Unchanged: `propose_sections` is read-only; `stage_enrollment` only stages;
`execute_enrollment_tx` re-validates at write. No tool gained an `approved` field. The
write-action safety + agent-node tests remain green; new tests in
`tests/unit/test_section_selection.py` cover the preference ranking and the
stage-error→LLM routing.

**Related fix (Piece 1).** `agent/graph.py` now routes a failed stage tool (a `ToolError`
with no `action_id`) **back to the LLM** instead of suspending at `interrupt` — so the
error reaches the student conversationally (e.g. offer the waitlist) instead of looping.

**Deferred.** Saved/named plans surfacing (A4) and a richer structured `SectionCard`
widget component are deferred to `STRETCH.md`; the chosen schedule is surfaced today via
the staged approval message text.

### D-R-016 — Behavior-preserving layering refactor; action-repo consolidation

**Decision.** A behavior-preserving refactor moved logic to its documented layer
without changing any behavior, prompts, SQL semantics, routes, or outputs:

- **Agent tools → application services.** Each `@tool` is now a thin adapter
  (validate via `args_schema` → resolve identity → delegate). Use-case
  orchestration lives in `services/{planning_service/,advising_service,
  enrollment_service,institutional_service,guidance_service}`; the tool files
  shrank ~74% (e.g. `planning.py` 1717→320). Services receive infra collaborators
  explicitly (`session_factory`, `llm`, …) and open the tenant session — so the
  per-tool-call unit-of-work boundary is unchanged.
- **Cross-cutting layers extracted:** all inline SQL → `repositories/` (entity
  repos: students/sections/programs/waitlist); LLM prompt/loops → `agent/llm/`;
  card/markdown builders → `presenters/`; row→engine-object mappers → `mappers/`.
- **Action-repo consolidation (F-5).** `ActionRepo` (in `services/actions`, which
  held inline action SQL) was **eliminated**; `ActionsRepository`
  (`repositories/core.py`) is the single home for staged-action CRUD. All SQL moved
  byte-identical; all call sites + tests updated.
- **Tenant-assert removed.** The unused `_assert_tenant` post-fetch hook (its only
  caller was the dead `ActionsRepository.get`; the live path never asserted) was
  removed, and `SECURITY.md §2.2` / DESIGN / SPEC / constitution updated to state
  the actual mechanism: **RLS (layer 1) + `WHERE tenant_id` filtering (layer 2)**.

**Why.** The as-built code had drifted from the documented layering (SQL,
presentation, and prompts inlined in agent tools). Moving each concern to its
documented home improves separation/testability with zero behavior change, guarded
at every step by ruff + mypy(strict) + the unit/write-safety suites. **No logic
changed** — SQL and prompt strings were verified byte-identical against the prior
committed versions.
