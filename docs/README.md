# Keel

**A sanctioned academic co-pilot that plans, predicts, advises — and safely acts.**

Keel is a multi-tenant SaaS where university students converse with an AI agent to plan their courses, predict academic risk, and receive personalized academic guidance. The agent can safely execute real actions — from course registration to graduation applications and prerequisite petitions — only after the student approves.

> *The AI proposes the course; the keel keeps it upright.*

<!-- TODO: CI badge once GitHub Actions configured -->
<!-- ![CI](https://github.com/<user>/keel/actions/workflows/ci.yml/badge.svg) -->

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/<user>/keel.git && cd keel

# 2. Configure
cp .env.example .env
# Edit .env with your LLM API key (all other secrets are auto-provisioned by Vault in dev mode)

# 3. Run
docker compose up --build

# 4. Verify
# API:           http://localhost:8000/healthz
# Admin console: http://localhost:3000
# Student widget: http://localhost:3001
# MLflow:         http://localhost:5555
# Vault:          http://localhost:8200
```

Two tenants are seeded automatically with a mock catalog (20+ courses, prerequisite chains, sections, student transcripts). Open the student widget, type a plain-language request, and watch the propose → verify → predict → explain → approve loop in action.

---

## What it does

| Capability | Description |
|-----------|-------------|
| **Plan** | Next-semester plans, full graduation paths, what-if simulations, saved/named plans, course swap, automatic replanning when the catalog changes |
| **Register** | Find conflict-free sections, enroll after approval, join/leave waitlists, seat-open notifications |
| **Advise** | RAG-grounded course info, degree audit, failure-recovery plans, major-switch recommendations, elective and career path guidance |
| **Predict** | Trained graduation-risk model (on-track / at-risk + mitigation), deterministic workload signal, GPA estimate (LLM baseline) |
| **Act** | Approval-gated enrollment, graduation applications, major-change requests, prerequisite petitions, advisor escalation (email + handoff summary) |

**The governing principle:** intelligence proposes, a deterministic engine verifies, models predict, and the student approves before anything is written. The LLM has real agency over the shape of a plan but can never emit an invalid one.

---

## Architecture

```
React Admin (registrar)              React Student Widget (signed token)
  catalog · rules · request queue      chat · plan view · risk badges · approval
     \____________________ FastAPI backend (RLS · layered · async) ____________________/
                                    |
  inbound → GUARDRAILS → CLASSIFIER ROUTER → [workflow | bounded LangGraph agent]
                                    |
  AGENT tools: audit_degree · propose_plan · simulate_whatif · predict_risk
               search_sections · save_plan · swap_course · execute_enrollment
               apply_graduation · request_major_change · submit_petition · escalate
                                    |
  ┌──────── DETERMINISTIC ENGINE (no LLM) ─────────┐
  │ prereq DAG · conflict checker · degree audit    │
  │ plan validator · workload index · greedy planner│
  └─────────────────────────────────────────────────┘
                                    |
  model-server (ONNX/joblib) · pgvector RAG (hybrid+rerank)
  Postgres+RLS · Redis+RQ · Vault · MinIO · MLflow
```

Full detail: [`ARCH.md`](ARCH.md)

---

## Project structure

```
keel/
├── src/keel/
│   ├── api/              # FastAPI routers (thin — parse, authorize, delegate)
│   ├── services/         # Use-cases, orchestration, action pattern
│   ├── repositories/     # All DB access (tenant-scoped, returns domain objects)
│   ├── domain/           # Pure business types + deterministic ENGINE
│   │   └── engine/       # DAG · audit · validator · sections · workload · planner
│   ├── infra/            # DB, Redis, Vault, MinIO, guardrails, tracing, outbox
│   ├── workers/          # RQ jobs: waitlist, outbox, alerts, auto-replan
│   └── agent/            # LangGraph graph, tool definitions, versioned prompts
├── model-server/         # Separate lean service (onnxruntime, no torch)
├── frontend/
│   ├── admin/            # React admin console
│   └── widget/           # React student widget + loader
├── tests/
│   ├── unit/             # Engine edge cases, domain logic
│   ├── eval/             # Golden sets, thresholds, RAGAS, red-team
│   └── integration/      # End-to-end API tests
├── migrations/           # Alembic
├── model_cards/          # Per-model documentation
├── docker-compose.yml
├── Makefile              # eval, lint, test, build shortcuts
├── .github/workflows/    # CI with eval gates
└── docs: CLAUDE.md · ARCH.md · PLAN.md · SPEC.md · EVALS.md
         SECURITY.md · DECISIONS.md · PLANNER.md · DATA.md · RUNBOOK.md
```

---

## Tech stack

| Technology | Purpose |
|-----------|---------|
| FastAPI + Pydantic | Async API, typed boundaries, structured I/O |
| PostgreSQL + RLS + Alembic | One DB, tenant isolation at the database, migrations |
| pgvector | RAG in the same DB, tenant-filtered |
| LangGraph | Bounded tool-calling agent (hard turns only) |
| Redis + RQ | Cache/session + background workers |
| ONNX Runtime / joblib | Lean model serving (no torch in containers) |
| MLflow | Experiment tracking + model registry (staging → production) |
| HashiCorp Vault | Secrets management at startup |
| MinIO | Model artifacts, eval reports |
| OpenTelemetry / LangSmith | End-to-end tracing |
| React | Admin console + embeddable student widget |
| Docker Compose | One-command stack |
| GitHub Actions | CI with eval gates |

---

## Evaluation gates

All gates run in CI and block merge on regression. See [`EVALS.md`](EVALS.md) for full detail.

| Gate | What it proves |
|------|---------------|
| Planner correctness | No plan violates any hard constraint (golden set, binary) |
| Intent classifier | Macro-F1 ≥ threshold (three-way ML / DL / LLM comparison) |
| Graduation-risk | Macro-F1 + at-risk recall ≥ thresholds (three-way comparison) |
| Tool selection | Agent picks the right tool on the golden set |
| RAG (RAGAS) | Faithfulness, relevancy, context recall on 25 triples |
| Red-team | Injection, cross-tenant, PII, write-safety — 100% refusal |
| Smoke | docker compose up → all healthchecks pass |

```bash
make eval          # run all gates
make eval-planner  # run a single gate
```

---

## Documentation

| Document | Purpose |
|----------|---------|
| [`CLAUDE.md`](CLAUDE.md) | Rules and conventions for Claude Code (auto-loaded each session) |
| [`ARCH.md`](ARCH.md) | Architecture: layers, data flow, subsystems |
| [`PLAN.md`](PLAN.md) | Build roadmap with checkboxes and acceptance criteria |
| [`SPEC.md`](SPEC.md) | Component contracts (schemas, invariants, edge cases) |
| [`EVALS.md`](EVALS.md) | Evaluation strategy, golden sets, metrics, thresholds |
| [`SECURITY.md`](SECURITY.md) | Threat model, tenant isolation, guardrails, write-action safety |
| [`DECISIONS.md`](DECISIONS.md) | Running log of non-obvious technical choices |
| [`PLANNER.md`](PLANNER.md) | Deterministic engine contract (inputs, violations, fallback) |
| [`DATA.md`](DATA.md) | Data sources, synthetic data honesty, FERPA rationale |
| [`RUNBOOK.md`](RUNBOOK.md) | How to run, rebuild, rotate secrets, promote models, replan |

---

## Demo

<!-- TODO: embed demo video link after recording (Day 8) -->
A demo video showing the full loop — widget → plain-language request → plan proposed with risk badge → student approves → enrollment executed + one institutional request filed — will be linked here.

---

## License

<!-- TODO: choose license -->
TBD