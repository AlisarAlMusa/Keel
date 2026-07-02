# Keel

**A sanctioned academic co-pilot that plans, predicts, advises — and safely acts.**

Keel is a multi-tenant SaaS a university deploys on top of its Student Information
System (Banner, Workday, PeopleSoft). Students chat with one AI agent embedded in
the registration portal to plan courses, predict academic risk, and get grounded
advice — and the agent can execute real actions (registration, waitlists,
graduation applications, prerequisite petitions) **only after the student approves**.

> *Intelligence proposes. The deterministic engine verifies. Models predict. Execution requires approval.*
> The LLM has real agency over the *shape* of a plan but can never emit an invalid one — the engine catches it first.

---

## The problem

Registration and degree planning are painful: hidden time conflicts, prerequisite
surprises, full sections, no advisor at 11 PM, and no straight answer to *"am I on
track to graduate?"* Students fall back on brittle scraping bots; official chatbots
only answer and route to a human; the SIS portal lets you act but can't reason.

Keel is the sanctioned middle ground — an assistant that integrates with the SIS and
can safely act on it, with tenant isolation, human approval before every write, and a
full audit trail. It doesn't just tell a student they're at risk; it builds a legal,
lower-risk plan, registers it on approval, and files the institutional paperwork that
normally requires an office visit.

## What it does

| Capability | Description |
|-----------|-------------|
| **Plan** | Next-semester plans, full graduation paths, what-if simulations, saved/named plans, course swap |
| **Register** | Find open conflict-free sections by preference, enroll after approval, join/leave waitlists, seat-open notifications |
| **Advise** | RAG-grounded course info, degree audit, failure-recovery plans, major-switch reasoning, elective & career guidance |
| **Predict** | Trained graduation-risk model (on-track / at-risk + mitigation), deterministic workload signal, caveated GPA estimate |
| **Act** | Approval-gated enrollment, graduation applications, major-change requests, petitions, advisor escalation |

Full feature catalogue (A1–G2): [`docs/OVERVIEW.md`](docs/OVERVIEW.md).

---

## The core idea

Three kinds of work live in three layers with hard boundaries:

| Layer | Owns | Can it be wrong? |
|-------|------|------------------|
| **LLM** | intent, constraint extraction, plan proposal, ranking, explanation, drafting | Yes — the engine catches it |
| **Engine** (deterministic, no LLM) | prerequisites, conflicts, capacity, credit caps, offering terms, coreqs, holds, eligibility | No — its verdict is final |
| **Models** | graduation-risk (trained), workload (deterministic) | Advisory only — never gate feasibility |

The planning loop is generate → verify → repair → predict → explain, not a one-shot pass:

```mermaid
flowchart LR
    P["🧠 LLM proposes<br/>2–3 candidate plans"] --> V{"⚙️ Engine verifies<br/>hard constraints"}
    V -- "structured violations" --> P
    V -- "valid only" --> M["📊 Models predict<br/>risk + workload"]
    M --> E["🧠 LLM ranks + explains"]
    E --> A(["✋ Student approves"])
    A --> WR["✍️ Write<br/>txn + outbox + audit"]
    G["🛟 Greedy planner<br/>(deterministic fallback)"] -. "if loop can't converge" .-> M
    classDef eng fill:#23354d,stroke:#5bc2e7,color:#f0ecdd;
    classDef gate fill:#3e8e7e,stroke:#f0ecdd,color:#f0ecdd;
    class V,G eng
    class A gate
```

A greedy deterministic planner is the fallback. Details: [`docs/ENGINE.md`](docs/ENGINE.md).

## Architecture

```mermaid
flowchart TD
    W["🎓 Student widget<br/>signed per-widget token"]
    K["🛠️ Keel console<br/>registrar admin · platform operator"]

    subgraph API["FastAPI backend · async · layered · RLS"]
        direction TB
        GR["🛟 Guardrails<br/>injection · cross-tenant · PII"]
        RT{"Classifier router<br/>trained · 15 labels"}
        AG["Bounded LangGraph agent"]
        WF["Deterministic workflow"]
        GR --> RT
        RT -- "confident" --> WF
        RT -- "ambiguous / multi" --> AG --> WF
    end

    subgraph CORE["⚙️ Deterministic engine — no LLM · verdict is final"]
        EN["prereq DAG · conflict checker · degree audit<br/>plan validator · workload index · greedy planner"]
    end

    MS["model-server<br/>intent · grad-risk<br/>joblib · torch-free"]
    RG["pgvector RAG<br/>hybrid + rerank"]
    DB[("Postgres + RLS<br/>SIS-domain + Keel-domain")]
    WK["RQ worker<br/>capacity · waitlist · outbox → email"]

    W -->|message| GR
    K --> API
    WF --> EN
    WF --> MS
    WF --> RG
    WF --> AP(["✋ Student approves"])
    AP --> TX["1 transaction:<br/>domain row + outbox event"]
    TX --> AU["audit log"]
    TX --> DB
    AU --> WK
    EN --- DB
    RG --- DB

    classDef eng fill:#23354d,stroke:#5bc2e7,color:#f0ecdd;
    classDef gate fill:#3e8e7e,stroke:#f0ecdd,color:#f0ecdd;
    classDef store fill:#02122f,stroke:#8ba3c5,color:#f0ecdd;
    class EN eng
    class AP gate
    class DB store
    style CORE fill:#23354d,stroke:#5bc2e7,color:#f0ecdd
```

Two logical domains share one Postgres, isolated by Row-Level Security: the **SIS-domain**
(catalog, sections, students, transcripts, enrollments, requests — seeded) and the
**Keel-domain** (plans, conversations, risk, RAG, config, audit, outbox, cost). In
production the SIS-domain leaves Keel's database behind a `SISGateway` adapter — designed,
not built in the demo. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/DESIGN.md`](docs/DESIGN.md) (as-built), and [`docs/PRODUCTION.md`](docs/PRODUCTION.md) (SIS seam).

---

## Quick start

```bash
# 1. Configure (defaults boot locally; no edits needed)
cp .env.example .env

# 2. Bring up the stack (api · worker · model-server · db · redis · minio · vault
#    · mlflow · jaeger · two mock SIS portals)
docker compose up -d --build

# 3. Migrate + seed two tenants (mock catalog, sections, students, transcripts)
docker compose exec api uv run alembic upgrade head
docker compose exec api uv run python -m scripts.seed

# 4. Open
#   Northane portal   http://localhost:3001   (student + registrar)
#   Summit portal     http://localhost:3002   (second tenant — proves isolation)
#   Keel console      http://localhost:8000/keel/   (admin + platform operator)
#   API docs          http://localhost:8000/docs
#   Jaeger traces     http://localhost:16686
```

Demo logins, service URLs, and a cross-tenant walkthrough are in
[`docs/RUNBOOK.md`](docs/RUNBOOK.md); a timed 6-minute script is in
[`docs/DEMO.md`](docs/DEMO.md). The app **fails closed** if Vault is unreachable
(try `docker compose stop vault && docker compose restart api`).

### Local development (no full stack)

```bash
uv sync                                  # backend env (api + worker)
uv run ruff check . && uv run mypy src scripts
uv run pytest tests/unit                 # engine + domain tests (no infra needed)
uv run pytest tests/eval                 # model + guardrail gates (committed artifacts)
cd model-server && uv sync && uv run pytest   # isolated, torch-free service
```

---

## Tech stack

| Technology | Purpose |
|-----------|---------|
| FastAPI + Pydantic v2 | Async API, typed boundaries, structured LLM/tool I/O |
| PostgreSQL + RLS + Alembic | One DB, two logical domains, tenant isolation at the database |
| pgvector + Cohere | Advising RAG (hybrid dense+sparse, RRF, multilingual rerank), tenant-filtered |
| LangGraph | Bounded tool-calling agent (hard turns only) |
| Gemini (lite + main tiers) | Cheap conversational turns vs. reasoning-heavy flows |
| Redis + RQ | Session/cache + background worker (capacity sync, waitlist, outbox) |
| model-server (joblib + sklearn) | Lean model serving — **no torch, no onnxruntime**; SHA-256-pinned |
| MLflow (Postgres + MinIO) | Experiment tracking + model registry (staging → production) |
| HashiCorp Vault | Secrets at startup (fail-closed) · **MinIO** artifacts |
| OpenTelemetry → Jaeger | End-to-end tracing (request → agent → tool → engine/DB/LLM) |
| React + Vite (npm workspace) | Student widget · Keel console · mock SIS portal · shared `@keel/ui` |
| Docker Compose · GitHub Actions | One-command stack · CI with eval gates |

Every choice is justified in [`docs/OVERVIEW.md`](docs/OVERVIEW.md) §14; non-obvious
choices are logged in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Evaluation gates

All gates run in CI (`.github/workflows/ci.yml`) and block merge on regression.
Thresholds live in `tests/eval/eval_thresholds.yaml`. Full detail: [`docs/EVALS.md`](docs/EVALS.md).

| Gate | Proves | Where |
|------|--------|-------|
| Planner correctness | No plan violates a hard constraint; every broken plan yields the expected violation | `tests/unit/test_engine_golden.py` |
| Intent classifier | Macro-F1 ≥ 0.75 + covered-accuracy + 100% obvious-case golden | `tests/eval/test_intent_gate.py` |
| Graduation-risk | Macro-F1 ≥ 0.77 + at-risk recall ≥ 0.68 + edge cases | `tests/eval/test_grad_risk_gate.py` |
| Tool selection | Router sends write/read/chitchat intents to the right node | `tests/eval/test_tool_selection.py` |
| Guardrails red-team | 100% of injection + cross-tenant probes refused | `tests/eval/test_redteam_gate.py` |
| PII redaction | Fake keys / emails / IDs never appear unredacted | `tests/eval/test_pii_gate.py` |
| Stack smoke | `docker compose up` → healthy, migrated, RLS policies present, integration tests pass | CI `smoke` job |

Two models are trained offline (Colab), compared three ways, and shipped as
SHA-256-pinned artifacts under `ml/*/artifacts/` with model cards. Provenance and
synthetic-data honesty: [`docs/DATA.md`](docs/DATA.md).

---

## Security highlights

- **Tenant isolation at three independent layers** — Postgres RLS (app connects as a
  `NOBYPASSRLS` role), repository-level scoping, and tenant-filtered pgvector.
- **Every write is approval-gated** — the LLM only *stages* a pending action; the write
  runs only after a student approves via a JWT-authenticated endpoint. No agent tool
  carries an `approved` field, so no injection can self-approve.
- **Guardrails on every message** — injection refusal, cross-tenant refusal, PII
  redaction; hardcoded, not weakenable by tenant config.
- **Secrets from Vault at startup** — the app refuses to boot if Vault is unreachable.

Threat model and guarantees: [`docs/SECURITY.md`](docs/SECURITY.md).

## Repository structure

```
keel/
├── src/keel/
│   ├── api/           # FastAPI routers (thin — parse, authorize, delegate)
│   ├── services/      # Use-cases, orchestration, the action pattern, agent host
│   ├── repositories/  # Tenant-scoped DB access (returns domain objects)
│   ├── domain/        # Pure business types + the deterministic ENGINE
│   │   └── engine/    # DAG · audit · verifier · sections · workload · planner
│   ├── infra/         # DB, Redis, Vault, MinIO, guardrails, RAG, LLM, tracing, email
│   ├── workers/       # RQ jobs: capacity sync, waitlist, outbox publisher
│   └── agent/         # LangGraph graph, tools, versioned prompts
├── model-server/      # Separate lean service (joblib + sklearn, torch-free)
├── frontend/          # npm workspace: ui · widget · admin · platform · portal
├── ml/                # Trained artifacts + model cards (intent · grad_risk)
├── data/              # Datasets (intent, grad-risk) + RAG corpus
├── migrations/        # Alembic (0001 … 0015)
├── tests/             # unit (engine) · eval (gates) · integration (through the API)
├── scripts/           # seed, dataset generators, model artifact sync
├── docker-compose.yml
└── docs/              # Documentation set (see below)
```

## Documentation

| Document | Answers |
|----------|---------|
| [`docs/OVERVIEW.md`](docs/OVERVIEW.md) | What is Keel, why, and every feature — the full vision |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the system is organized (intended shape) |
| [`docs/DESIGN.md`](docs/DESIGN.md) | How it's actually built today (as-built design of record) |
| [`docs/PRODUCTION.md`](docs/PRODUCTION.md) | The SIS boundary — demo vs. production |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, tenant isolation, write-action safety |
| [`docs/SPEC.md`](docs/SPEC.md) | Component contracts (schemas, invariants, edge cases) |
| [`docs/ENGINE.md`](docs/ENGINE.md) | The deterministic engine + plan generation/verification loop |
| [`docs/DATA.md`](docs/DATA.md) | Datasets, provenance, synthetic-data honesty |
| [`docs/EVALS.md`](docs/EVALS.md) | Evaluation strategy, gates, thresholds |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Non-obvious technical choices, with rationale |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Run, troubleshoot, demo credentials, model promotion |
| [`docs/DEMO.md`](docs/DEMO.md) | 6-minute demo script |
| [`docs/STRETCH.md`](docs/STRETCH.md) | Deferred work, with trade-offs and triggers |
| [`CLAUDE.md`](CLAUDE.md) | Build rules and conventions (agent contract, auto-loaded) |

The specification-driven history (per-phase spec/plan/tasks) lives under
[`specs/`](specs/).

## License

Released under the [MIT License](LICENSE).
