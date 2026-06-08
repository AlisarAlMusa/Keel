---
description: "Task list for Phase 0 — Foundation"
---

# Tasks: Phase 0 — Foundation

**Input**: Design documents from `/specs/001-phase-0-foundation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Phase 0 is scaffolding. Tests are **minimal and targeted** — only where they verify a Success Criterion directly (config validation, Vault fail-closed, migration+RLS, seed counts). No engine/agent tests (those phases are later). The CI smoke test is the headline verification.

**Organization**: Grouped by user story. Story priorities from spec.md: US1 (P1), US2 (P1), US3 (P1), US4 (P2), US5 (P2).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5; Setup/Foundational/Polish have no story label
- All paths are repository-relative from repo root

## Architecture invariants (apply to every task)

- **Two packages**: root `keel` (`src/keel/`, powers api+worker) and isolated `model-server/`. Never add backend deps to `model-server`; never add `torch` anywhere.
- **Three images**: `Dockerfile.api`, `Dockerfile.worker` (root context, shared root `.dockerignore`), `model-server/Dockerfile` (own context + own `.dockerignore`).
- **Layered direction**: `api → services → repositories → domain`; `infra` injected via DI; `domain/` imports no framework/IO. Never import upward.
- **uv only**: `uv add`/`uv sync`/`uv run`; both `uv.lock` committed; Docker uses `uv sync --frozen --no-dev`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repo layout, both Python packages, lockfiles, ignore/env files.

- [X] T001 Create the full directory tree per plan.md with `__init__.py` files: `src/keel/{api/routers,services/actions,repositories,domain/engine,infra,workers,agent/prompts}`, `tests/{unit,integration,eval}`, `migrations/versions`, `scripts/`, `model-server/src/model_server/`, `frontend/admin`, `frontend/widget`, `.github/workflows/` (add `.gitkeep` to the two frontend dirs)
- [X] T002 Create root `pyproject.toml` for the `keel` backend package: Python 3.12, hatchling build of `src/keel`, runtime deps (fastapi, uvicorn[standard], pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, alembic, redis, rq, hvac, boto3, structlog, opentelemetry-api/sdk/instrumentation-fastapi/exporter-otlp, langsmith), dev deps (ruff, mypy, pytest, pytest-asyncio, types-boto3 as needed); ruff + mypy config tables
- [X] T003 [P] Create `.python-version` with `3.12` at repo root
- [X] T004 [P] Create `model-server/pyproject.toml` (lean package `model_server`): Python 3.12, hatchling build of `model-server/src/model_server`, runtime deps (fastapi, uvicorn[standard], pydantic, pydantic-settings, onnxruntime, joblib, numpy), dev deps (ruff, mypy, pytest) — **no torch, no sqlalchemy, no langgraph**; plus `model-server/.python-version` = `3.12`
- [X] T005 Generate and commit both lockfiles: `uv lock` at root and `uv lock` in `model-server/` (produces root `uv.lock` and `model-server/uv.lock`)
- [X] T006 [P] Create `.gitignore` (ignore `.venv`, `__pycache__`, `*.pyc`, `.env`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `dist/`, build artifacts, `node_modules/`)
- [X] T007 [P] Create `.env.example` documenting every key from `contracts/env-contract.md` (non-secret config + Vault coordinates; placeholders only, no real secrets)

**Checkpoint**: `uv sync` succeeds in both packages; repo tree matches plan.md.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config, logging, domain types, ORM, infra adapters, Alembic scaffold — shared by all user stories. ⚠️ No user story may begin until this completes.

- [X] T008 [P] Implement `src/keel/config.py` (pydantic-settings `Settings`, `extra="forbid"`, typed fields for every env key in `contracts/env-contract.md`; `os.getenv` confined here; no secret defaults)
- [X] T009 [P] Implement `src/keel/logging.py` (structlog JSON renderer; fields event/level/timestamp/service/request_id/trace_id/tenant_id; helper to bind context; no `print`)
- [X] T010 [P] Implement `src/keel/domain/exceptions.py` (typed domain exceptions: `NotFoundError`, `PermissionDeniedError`, `ToolFailureError`, `ExternalServiceError`)
- [X] T011 [P] Implement `src/keel/domain/models.py` — Pydantic v2 `frozen=True` value objects per SPEC.md §1 (Term, DayOfWeek, TimeSlot with `overlaps()`, Course, Section, Prerequisite, Corequisite, TranscriptEntry, Hold, ProgramRequirement, Student) — pure, no framework/IO imports
- [X] T012 Implement `src/keel/infra/orm.py` — SQLAlchemy 2.x DeclarativeBase + ORM classes for all 16 tables per data-model.md (SEPARATE from domain models; this is the metadata Alembic targets)
- [X] T013 Implement `src/keel/infra/db.py` (async engine from `DATABASE_URL`, async session factory, `set_tenant(session, tenant_id)` issuing `SET LOCAL app.tenant_id`, app connects as non-superuser `keel_app`)
- [X] T014 [P] Implement `src/keel/infra/vault.py` (hvac client factory + `load_secrets()` that reads required keys from `VAULT_SECRET_PATH`; raises on unreachable/missing — fail-closed loader used by US2)
- [X] T015 [P] Implement `src/keel/infra/tracing.py` (OTel SDK + FastAPI instrumentation init; OTLP exporter; degrades gracefully if `OTEL_EXPORTER_OTLP_ENDPOINT` empty; LangSmith env wiring)
- [X] T016 [P] Implement `src/keel/infra/redis.py` (async redis client factory from `REDIS_URL`) and `src/keel/infra/storage.py` (boto3 S3/MinIO client factory; idempotent bucket-ensure)
- [X] T017 Create Alembic scaffold: `alembic.ini`, `migrations/env.py` (async, targets `infra/orm.py` metadata, reads `DATABASE_URL`), `migrations/script.py.mako`

**Checkpoint**: Config loads, domain types import with no framework deps, ORM metadata importable, Alembic configured.

---

## Phase 3: User Story 1 — One-command local stack stands up healthy (Priority: P1) 🎯 MVP

**Goal**: `docker compose up` brings all eight services to healthy; API and model-server answer health endpoints.

**Independent Test**: From a clean checkout, run the bring-up command; confirm all eight services healthy and `/healthz` returns 200 on api (8000) and model-server (9000).

- [X] T018 [P] [US1] Implement `src/keel/api/routers/health.py` (`GET /healthz` liveness, `GET /readyz` readiness checking vault/db/redis) per `contracts/health-endpoints.md`
- [X] T019 [P] [US1] Implement `src/keel/api/deps.py` (DI providers: settings, DB session, redis, storage, model-server client — from lifespan singletons, never constructed per-request)
- [X] T020 [US1] Implement `src/keel/main.py` (FastAPI app + lifespan: init logging+tracing, load Vault secrets, build singletons DB/redis/storage, ensure MinIO bucket, mount health router; store singletons on app.state)
- [X] T021 [P] [US1] Implement `src/keel/workers/main.py` (RQ worker entrypoint bound to `keel` queue on `REDIS_URL`; structured logging)
- [X] T022 [P] [US1] Create structural placeholders so later phases land correctly: `src/keel/repositories/base.py` (tenant-scoped base repo: requires tenant_id, post-fetch assertion — interface only), `src/keel/services/actions/__init__.py` (action-pattern home docstring), `src/keel/domain/engine/__init__.py` (crown-jewel home docstring)
- [X] T023 [P] [US1] Implement `model-server/src/model_server/config.py` (pydantic-settings) and `model-server/src/model_server/main.py` (FastAPI app + `GET /healthz` returning `{"status":"ok","service":"model-server","models":[]}`)
- [X] T024 [P] [US1] Create root `.dockerignore` (shared api+worker context: exclude `.venv`, caches, `.git`, `frontend/`, `model-server/`, `specs/`, `tests/`, `.env`)
- [X] T025 [P] [US1] Create `Dockerfile.api` (multi-stage; `uv sync --frozen --no-dev`; CMD `uvicorn keel.main:app --host 0.0.0.0 --port 8000`; curl for healthcheck)
- [X] T026 [P] [US1] Create `Dockerfile.worker` (root context; `uv sync --frozen --no-dev`; CMD `rq worker -u redis://redis:6379/0 keel`)
- [X] T027 [P] [US1] Create `model-server/.dockerignore` and `model-server/Dockerfile` (own context; `uv sync --frozen --no-dev`; CMD `uvicorn model_server.main:app --host 0.0.0.0 --port 9000`)
- [X] T028 [US1] Create `docker-compose.yml` with 8 services + healthchecks + named volumes + network per `contracts/health-endpoints.md`: `db` (pgvector/pgvector:pg16, non-superuser `keel_app`, volume), `redis`, `minio` (+volume, console 9001), `vault` (dev mode), a one-shot `vault-init` writing placeholder secrets to `secret/keel/app`, `mlflow` (backed by db + minio), `api` (depends_on db/redis/vault healthy), `worker` (depends_on db/redis), `model-server`; no hardcoded env-specific URLs (resolve from env)

**Checkpoint**: `docker compose up -d --build` → all healthy; both `/healthz` return 200; restart preserves volumes (SC-001, SC-007).

---

## Phase 4: User Story 2 — Application refuses to run without its secrets (Priority: P1)

**Goal**: App boots when Vault is reachable+seeded; refuses to boot (fail closed) when Vault is unreachable; no secret leaks to logs/traces.

**Independent Test**: Start with Vault up (boots); stop Vault and restart api (refuses to boot with a clear, non-sensitive error).

- [X] T029 [US2] Wire the fail-closed Vault gate into `src/keel/main.py` lifespan: call `infra/vault.load_secrets()` before building any secret-dependent singleton; on unreachable/error/missing-required-key, log a clear non-sensitive message and re-raise so the process exits non-zero (depends on T014, T020)
- [X] T030 [US2] Merge Vault-sourced secrets into runtime config (e.g., `db_password` into the async DSN, MinIO keys into the storage client) without ever logging secret values
- [X] T031 [P] [US2] Add `tests/unit/test_vault_failclosed.py` (mock hvac: reachable+seeded → `load_secrets` returns dict; unreachable/missing → raises; assert error message contains no secret value)
- [X] T032 [P] [US2] Add `tests/unit/test_config.py` (valid env loads; unknown key rejected by `extra="forbid"`; required-without-default raises) verifying FR-013

**Checkpoint**: Stopping Vault makes api fail to boot 100% of the time; logs/traces contain no secret (SC-002, SC-006).

---

## Phase 5: User Story 3 — Database created with tenant isolation enforced by the database (Priority: P1)

**Goal**: `alembic upgrade head` on a clean DB creates all 16 tables + extensions + RLS (enabled & forced) with a `tenant_isolation` policy on all 15 tenant-owned tables; downgrade returns to empty.

**Independent Test**: Apply migration to empty DB; assert 16 tables and 15 `tenant_isolation` policies; downgrade → empty.

- [X] T033 [US3] Implement `migrations/versions/0001_baseline.py` `upgrade()`: `CREATE EXTENSION IF NOT EXISTS pgcrypto` and `vector`; `op.create_table` for all 16 tables per data-model.md (types, CHECKs, FKs, uniques, partial-unique one-active-plan index, JSONB columns)
- [X] T034 [US3] In the same migration, add RLS to the 15 tenant-owned tables via `op.execute`: `ENABLE` + `FORCE ROW LEVEL SECURITY` + `CREATE POLICY tenant_isolation ... USING/WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)`; ensure `keel_app` is a non-superuser role with table privileges (and is the role used by the app)
- [X] T035 [US3] Implement `downgrade()`: drop policies and tables in reverse dependency order (reversible to empty) — FR-018
- [X] T036 [P] [US3] Add `tests/integration/test_migration_rls.py` (against a test Postgres: upgrade → assert 16 tables in `pg_tables` and 15 rows in `pg_policies` named `tenant_isolation`; downgrade → assert empty) verifying SC-003

**Checkpoint**: Migration up/down verified; RLS present on every tenant-owned table.

---

## Phase 6: User Story 4 — Representative seed data exists for development (Priority: P2)

**Goal**: Seed 2 tenants, each ≥20 courses with prereq chains, sections, program requirements, 2 transcripts; catalog text uploaded to MinIO per tenant.

**Independent Test**: Run seed on a migrated DB; assert 2 tenants with required counts; assert catalog objects exist in MinIO.

- [X] T037 [US4] Implement `scripts/seed.py`: for 2 tenants, set `app.tenant_id`, insert ≥20 courses with realistic prereq chains (acyclic), corequisites, sections (terms/years/slots/capacity), program_requirements, 1 student each with a 2-term transcript (2 transcripts total per spec), all correctly tenant-scoped via `infra/db` (depends on T012, T013)
- [X] T038 [US4] In `scripts/seed.py`, upload each tenant's catalog descriptive text to MinIO via `infra/storage` (tenant-prefixed keys) — FR-021
- [X] T039 [US4] Make seed predictable on re-run / unmigrated DB: detect existing data and either reset deterministically or refuse with a clear message; never write partial/inconsistent data — FR-022
- [X] T040 [P] [US4] Add `tests/integration/test_seed.py` (after migrate+seed: assert exactly 2 tenants, each ≥20 courses + prereqs + sections + program_reqs + 2 transcripts; assert MinIO has catalog text per tenant) verifying SC-004

**Checkpoint**: Two fully-populated tenants exist; cross-tenant isolation is now testable in later phases.

---

## Phase 7: User Story 5 — Continuous integration proves the foundation green (Priority: P2)

**Goal**: CI runs lint + type-check + image build + compose smoke and is green for the delivered foundation.

**Independent Test**: Open a trivial PR; CI runs all four checks and reports green; an intentional lint/type error fails with an identifiable cause.

- [X] T041 [P] [US5] Create `tests/conftest.py` (pytest-asyncio config, shared fixtures) and `tests/eval/eval_thresholds.yaml` (placeholder; gates added per future phase) — keeps `pytest` runnable and reserves the eval-gate home
- [X] T042 [US5] Create `.github/workflows/ci.yml`: matrix/steps to `uv sync` both packages, `ruff check` + `ruff format --check`, `mypy src` (and model-server), `pytest`, build all three images, then `docker compose up -d` smoke that polls api+model-server `/healthz` and asserts services healthy; fail with identifiable cause per check (FR-023, FR-024)
- [X] T043 [P] [US5] Write `README.md` run instructions (one-command bring-up, verify steps, local dev) mirroring `quickstart.md`; add CI badge placeholder

**Checkpoint**: CI green on the delivered scaffold; intentional violations fail visibly (SC-005).

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T044 [P] Start `DECISIONS.md` with the foundational decisions: (1) two-package / three-image architecture and why api+worker share a package; (2) database-enforced RLS isolation approach; (3) uv-only + frozen installs; (4) ORM-separate-from-domain; (5) deferring LangGraph to Phase 2 (FR-025)
- [X] T045 [P] Verify layered dependency direction holds in the delivered scaffold (no upward imports; `domain/` free of framework/IO imports) — SC-008
- [X] T046 Run `quickstart.md` end-to-end from a fresh state (up → migrate → seed → verify → fail-closed check → teardown) and fix any drift
- [X] T047 [P] Update `PLAN.md` Phase 0 checkboxes to reflect delivered items

---

## Dependencies & Execution Order

### Phase dependencies
- **Setup (P1)**: no deps — start immediately.
- **Foundational (P2)**: depends on Setup — **blocks all user stories**.
- **US1 / US2 / US3 (all P1)**: depend on Foundational. US2 depends on US1's lifespan (T020) for the gate wiring; US3 is independent of US1/US2 (only needs Foundational ORM+Alembic). US1 and US3 can proceed in parallel.
- **US4 (P2)**: depends on US3 (needs the migrated schema).
- **US5 (P2)**: depends on US1 (images + compose to smoke) and benefits from US2–US4 tests existing.
- **Polish**: after desired stories complete.

### Story independence notes
- **US3** is the most independent (pure migration) — could be demoed alone against a DB.
- **US1** is the MVP spine (healthy stack).
- **US2** layers the security gate onto US1's lifespan.

### Parallel opportunities
- Setup: T003, T004, T006, T007 in parallel (after T001/T002).
- Foundational: T008, T009, T010, T011, T014, T015, T016 in parallel; T012→T013→T017 more sequential (ORM before db helpers/alembic env reference).
- US1: T018, T019, T021, T022, T023, T024, T025, T026, T027 in parallel; then T020 (lifespan) and T028 (compose) integrate.
- US2: T031, T032 parallel. US3: T036 parallel with doc. US4: T040 parallel. US5: T041, T043 parallel.

---

## Parallel Example: User Story 1

```text
# After Foundational completes, launch these together (different files):
T018 health router · T019 deps · T021 worker entrypoint · T023 model-server app
T024 root .dockerignore · T025 Dockerfile.api · T026 Dockerfile.worker · T027 model-server Docker
# Then integrate:
T020 main.py lifespan  →  T028 docker-compose.yml  →  bring up & verify
```

---

## Implementation Strategy

### MVP first (US1 only)
1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → **STOP & VALIDATE**: stack healthy from fresh clone. That alone is a demoable foundation.

### Incremental delivery
US1 (healthy stack) → US3 (schema+RLS) → US2 (fail-closed) → US4 (seed) → US5 (CI green) → Polish. Each adds value without breaking the previous.

---

## Notes
- [P] = different files, no incomplete-task dependency.
- Keep the engine/agent/model logic OUT — Phase 0 is empty-but-healthy scaffolding only.
- Commit after each logical group; the git extension auto-commit hooks can checkpoint between phases.
- Total: **47 tasks** — Setup 7, Foundational 10, US1 11, US2 4, US3 4, US4 4, US5 3, Polish 4.
