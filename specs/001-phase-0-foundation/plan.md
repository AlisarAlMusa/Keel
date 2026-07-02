# Implementation Plan: Phase 0 — Foundation

**Branch**: `001-phase-0-foundation` | **Date**: 2026-06-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-phase-0-foundation/spec.md`

## Summary

Phase 0 stands up the Keel foundation: a layered Python backend (`api → services → repositories → domain`, `infra` injected), a separate lean model-server, a one-command Docker Compose stack of all eight services, Vault-gated startup (fail closed), OpenTelemetry/LangSmith tracing, an Alembic baseline migration creating 16 tables with PostgreSQL Row-Level Security on every tenant-owned table, a seed script (2 tenants, ≥20 courses with prereq chains, sections, transcripts, catalog text to MinIO), and a green GitHub Actions CI skeleton (ruff + mypy + image build + compose smoke). This phase is **scaffolding only** — the app and model-server are empty-but-healthy; no engine, agent, or model-training logic ships here.

The defining structural decision is the **two-package / three-image** build architecture, documented as a first-class section below.

## Technical Context

**Language/Version**: Python 3.12 (async throughout; no sync DB calls in request paths)

**Primary Dependencies**:
- Backend (`keel`): FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2.x (async), asyncpg, Alembic, redis, rq, hvac (Vault), boto3 (MinIO/S3), structlog, OpenTelemetry (api/sdk/instrumentation-fastapi/exporter-otlp), LangSmith. *(LangGraph/LangChain deferred to Phase 2 when the agent lands — not installed in Phase 0 to keep the image lean.)*
- Model-server (`model-server`): FastAPI, Uvicorn, Pydantic v2, pydantic-settings, onnxruntime, joblib, numpy. **No torch, no SQLAlchemy, no LangGraph.**

**Storage**: PostgreSQL 16 + pgvector (one instance, RLS-enforced isolation); MinIO (S3-compatible object store) for artifacts and catalog text; Redis for cache/session + RQ broker.

**Testing**: pytest + pytest-asyncio; ruff (lint+format) and mypy (type check) in CI; Docker Compose smoke test.

**Target Platform**: Linux containers via Docker Compose; local developer machines and CI runners.

**Project Type**: Multi-tenant web service (two backend containers from one package + one isolated model-server) with two later React frontends (scaffold dirs only this phase).

**Performance Goals**: Not a Phase 0 concern beyond "stack reaches healthy." Async request path established so later phases meet latency budgets.

**Constraints**: Fail-closed on missing secrets; no hardcoded secrets; structured JSON logs with `tenant_id`/`trace_id` and no secret leakage; durable stores survive restart; layered dependency direction enforced.

**Scale/Scope**: One Postgres instance to ~hundreds of tenants (documented MVP ceiling); two seeded tenants for development.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Phase 0 relevance | Status |
|-----------|-------------------|--------|
| I. Three-Layer Boundary | No LLM/engine/models in Phase 0; the *structure* that enforces the boundary (layered dirs, `domain/engine/` placeholder, `services/actions/` placeholder) is created so later code lands in the right place. | ✅ PASS — boundary-preserving structure created; no logic that could violate it. |
| II. Spec-Before-Code | This `plan.md` follows `spec.md`; `docs/SPEC.md`/`docs/ARCHITECTURE.md` already define later contracts. No engine/action code is written in Phase 0, so the "human writes edge-case tests first" gate is **not yet triggered** (Phase 1). | ✅ PASS — flagged: engine edge-case tests are a Phase 1 human gate. |
| III. Verifier-Gated Surfaces | No plan surfaces and no writes happen in Phase 0. The `outbox` + `audit_log` tables and `services/actions/` package placeholder are created so the pattern has a home. | ✅ PASS — no surfaces/writes exist to gate yet. |
| IV. Defense-in-Depth Tenant Isolation (NON-NEGOTIABLE) | RLS on every tenant-owned table is delivered in the baseline migration (layer 1). Repository-layer filtering (layer 2) and pgvector filtering (layer 3) get their structure (base repo, pgvector column) but enforcement logic lands with the features that use them. | ✅ PASS — layer 1 enforced now; layers 2–3 scaffolded. |
| V. Continuous Eval Gates | CI skeleton (ruff, mypy, build, smoke) lands this phase; eval gates are added the day each feature lands (later phases). `tests/eval/eval_thresholds.yaml` seeded as a placeholder. | ✅ PASS — CI from Day 1. |
| VI. Honest by Design | No models/claims yet. `docs/DECISIONS.md` started; synthetic seed data documented as illustrative. | ✅ PASS. |
| VII. Bounded Intelligence | No agent in Phase 0. `agent/` scaffolded empty; LangGraph not installed yet. | ✅ PASS. |

**Technology stack mandates**: uv-only ✅; no torch in any container ✅ (model-server is onnxruntime/joblib only); Vault fail-closed ✅; one Postgres + pgvector ✅; async throughout ✅. **No violations — Complexity Tracking not required.**

## Build & Packaging Architecture (first-class decision)

This is the structural backbone of the repository and the most consequential Phase 0 decision. It is recorded here and in `docs/DECISIONS.md`.

### Two Python packages, two isolated environments

| Package | Location | `pyproject.toml` | `uv.lock` | venv | Powers | Dependency profile |
|---------|----------|------------------|-----------|------|--------|--------------------|
| **`keel`** (backend) | repo root, code in `src/keel/` | root `pyproject.toml` | root `uv.lock` | root `.venv` | **api** + **worker** containers | Full backend: FastAPI, SQLAlchemy/asyncpg, Alembic, redis/rq, hvac, boto3, OTel, structlog |
| **`model-server`** | `model-server/` | `model-server/pyproject.toml` | `model-server/uv.lock` | `model-server/.venv` | **model-server** container | Lean serving: FastAPI, onnxruntime, joblib, numpy. **No torch / SQLAlchemy / LangGraph.** |

**Why exactly two packages (not one, not per-container):**
- **api and worker are the same codebase.** The worker (`src/keel/workers/`) imports `domain/`, `services/`, `repositories/`, `infra/` exactly as the API does. They differ only in entrypoint (`uvicorn keel.main:app` vs `rq worker`). Splitting them into separate packages would duplicate the entire dependency tree and source for zero benefit — rejected as overengineering.
- **model-server must be isolated.** Constitution mandate: no torch in any runtime container; the server stays lean (`onnxruntime`/`joblib` only). A shared package would drag the backend's heavy deps (SQLAlchemy, LangGraph later) into the model image. A hard package boundary makes "no torch / no heavy deps in the model image" true *by construction*, not by discipline.

### Three container images, three Dockerfiles

| Image | Dockerfile | Build context | `.dockerignore` | Entrypoint |
|-------|-----------|---------------|------------------|------------|
| **api** | `Dockerfile.api` (repo root) | repo root | root `.dockerignore` (shared) | `uvicorn keel.main:app --host 0.0.0.0 --port 8000` |
| **worker** | `Dockerfile.worker` (repo root) | repo root | root `.dockerignore` (shared) | `rq worker -u redis://redis:6379 keel` |
| **model-server** | `model-server/Dockerfile` | `model-server/` | `model-server/.dockerignore` | `uvicorn model_server.main:app --host 0.0.0.0 --port 9000` |

- `Dockerfile.api` and `Dockerfile.worker` **share the root build context and one root `.dockerignore`** — same source tree, same package, same exclusions. Two identical `.dockerignore` files would be pure duplication; one root file is the clean choice.
- `model-server/` is a **self-contained context** with its own `Dockerfile` and `.dockerignore`.
- Every Dockerfile installs deps with `uv sync --frozen --no-dev` from the committed `uv.lock` (reproducible, no dev deps in runtime images). Multi-stage builds keep runtime layers small.

### uv usage rules
- `uv` is the only package manager. `uv add` to add, `uv sync` to install, `uv run` to execute. Never `pip install`.
- Both `uv.lock` files are committed (root and `model-server/`).
- CI and Docker use `--frozen` to fail if the lockfile is stale.

## Project Structure

### Documentation (this feature)

```text
specs/001-phase-0-foundation/
├── plan.md              # This file
├── research.md          # Phase 0 output — tech decisions resolved
├── data-model.md        # Phase 1 output — 16-table schema + RLS
├── quickstart.md        # Phase 1 output — one-command bring-up & verify
├── contracts/           # Phase 1 output — health endpoints, env contract
│   ├── health-endpoints.md
│   └── env-contract.md
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
.
├── pyproject.toml                 # keel backend package (api + worker)
├── uv.lock                        # backend lockfile (committed)
├── .python-version                # 3.12
├── .dockerignore                  # shared root context exclusions (api + worker)
├── Dockerfile.api                 # api image (uvicorn)
├── Dockerfile.worker              # worker image (rq)
├── docker-compose.yml             # 8 services: api, worker, model-server, db, redis, minio, vault, mlflow
├── alembic.ini
├── .env.example                   # documents every config value
├── .gitignore
├── README.md                      # run instructions stub
├── docs/DECISIONS.md                   # started this phase
│
├── src/keel/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app + lifespan (Vault gate, tracing, singletons)
│   ├── config.py                  # pydantic-settings (typed, extra="forbid")
│   ├── logging.py                 # structlog JSON config (tenant_id/trace_id fields)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py                # DI providers (DB session, settings, clients)
│   │   └── routers/
│   │       ├── __init__.py
│   │       └── health.py          # /healthz, /readyz
│   ├── services/
│   │   ├── __init__.py
│   │   └── actions/               # action-pattern home (placeholder)
│   │       └── __init__.py
│   ├── repositories/
│   │   ├── __init__.py
│   │   └── base.py                # tenant-scoped base repo (structure only)
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── models.py              # SPEC §1 Pydantic v2 frozen value objects
│   │   ├── exceptions.py          # NotFoundError, PermissionDeniedError, etc.
│   │   └── engine/
│   │       └── __init__.py        # crown-jewel home (empty in Phase 0)
│   ├── infra/
│   │   ├── __init__.py
│   │   ├── db.py                  # async engine + session factory + tenant context
│   │   ├── orm.py                 # SQLAlchemy ORM models (SEPARATE from domain)
│   │   ├── vault.py               # hvac client; fail-closed secret loader
│   │   ├── tracing.py             # OTel + LangSmith init
│   │   ├── redis.py               # redis client factory
│   │   └── storage.py             # MinIO/boto3 client factory
│   ├── workers/
│   │   ├── __init__.py
│   │   └── main.py                # RQ worker entrypoint
│   └── agent/
│       ├── __init__.py
│       └── prompts/
│           └── __init__.py
│
├── migrations/                    # Alembic
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_baseline.py       # 16 tables + RLS + pgvector
│
├── scripts/
│   └── seed.py                    # 2 tenants, ≥20 courses, sections, transcripts, MinIO
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/__init__.py
│   ├── integration/__init__.py
│   └── eval/
│       ├── __init__.py
│       └── eval_thresholds.yaml   # placeholder; gates added per-phase
│
├── model-server/
│   ├── pyproject.toml             # lean package
│   ├── uv.lock                    # model-server lockfile (committed)
│   ├── .python-version
│   ├── Dockerfile                 # own context
│   ├── .dockerignore              # own context
│   └── src/model_server/
│       ├── __init__.py
│       ├── main.py                # FastAPI app + /healthz (empty-but-healthy)
│       └── config.py
│
├── frontend/
│   ├── admin/.gitkeep             # React admin console (Phase 5)
│   └── widget/.gitkeep            # React student widget (Phase 5)
│
└── .github/workflows/
    └── ci.yml                     # ruff + mypy + build + compose smoke
```

**Structure Decision**: Multi-tenant web service. The backend is one `uv`-managed package (`src/keel/`) following the mandated layered structure (`api → services → repositories → domain`, `infra` injected), producing **two** container images (api, worker) that run identical code with different entrypoints. The `model-server/` is a **second, isolated** `uv` package producing the third image, kept free of heavyweight dependencies. Frontends are scaffolded as placeholders for Phase 5. This realizes the two-package / three-image architecture documented above.

## Complexity Tracking

> No constitution violations. The two-package / three-image split is the *simplest* structure that satisfies the no-torch-in-containers mandate and the api/worker shared-code reality — not added complexity. No table needed.
