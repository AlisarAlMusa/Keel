# Research: Phase 0 — Foundation

Phase 0 technical decisions. Most are dictated by `CLAUDE.md` §6 and the constitution's technology mandates (substitutions require an amendment), so "alternatives considered" is documented for honesty even where the choice is fixed.

## R1. Package & environment management — `uv`

- **Decision**: `uv` (0.11.x) is the sole package manager. Two projects: root `keel` and `model-server/`, each with `pyproject.toml` + committed `uv.lock`. Python pinned to 3.12 via `.python-version` (uv downloads it; host has 3.11).
- **Rationale**: Constitution mandate. uv is fast, deterministic (lockfile), and replaces pip/pip-tools/virtualenv. `uv sync --frozen --no-dev` gives reproducible runtime images.
- **Alternatives considered**: pip + requirements.txt (no lockfile determinism), Poetry (slower, heavier) — both rejected by mandate.

## R2. Two-package / three-image architecture

- **Decision**: See `plan.md` → "Build & Packaging Architecture." Backend = one package (api+worker images); model-server = isolated package (third image).
- **Rationale**: api/worker share 100% of code (different entrypoint only) → one package. model-server must be torch-free and lean → hard package boundary enforces it by construction.
- **Alternatives considered**: (a) single mono-package with optional extras for model-server — rejected: a careless `uv sync` could still pull heavy deps into the model image, and the constitution wants the boundary structural. (b) four packages (api/worker/model/shared) — rejected: api and worker have no divergent deps, so the split is pure overhead.

## R3. Web framework & async — FastAPI + Uvicorn + asyncio

- **Decision**: FastAPI on Uvicorn; async throughout. Pydantic v2 at all boundaries; pydantic-settings for config.
- **Rationale**: Mandate. Typed boundaries, async I/O, DI via `Depends`, lifespan for singletons.
- **Alternatives considered**: Flask/Django (sync-first) — rejected by async mandate.

## R4. Database — PostgreSQL 16 + pgvector + SQLAlchemy async + Alembic

- **Decision**: One Postgres 16 instance; `pgvector` extension in the same DB; SQLAlchemy 2.x async ORM with `asyncpg`; Alembic for migrations. Image: `pgvector/pgvector:pg16` (Postgres 16 with the extension prebuilt).
- **Rationale**: Mandate (one DB, RLS, pgvector co-located). `asyncpg` is the fastest async driver. Alembic is the standard migration tool and version-tracks schema.
- **RLS approach**: Each request sets a session-local GUC (`SET LOCAL app.tenant_id = '<uuid>'`); RLS policies use `current_setting('app.tenant_id')::uuid`. The app connects as a non-superuser role so RLS is enforced (superusers/`BYPASSRLS` skip policies). The migration enables RLS and `FORCE ROW LEVEL SECURITY` on every tenant-owned table.
- **Alternatives considered**: schema-per-tenant (more isolation, heavier ops — documented future option beyond hundreds of tenants); separate vector DB (Qdrant/Pinecone) — rejected by "no second database" mandate.

## R5. ORM models separate from domain models

- **Decision**: SQLAlchemy ORM classes live in `infra/orm.py`. Domain value objects (Pydantic v2 `frozen=True`, per `docs/SPEC.md` §1) live in `domain/models.py`. Repositories map ORM rows → domain objects and never leak ORM rows upward.
- **Rationale**: Engineering rule #9 + constitution layering. Keeps `domain/` import-free of frameworks (the engine must be pure). The DB schema can evolve without changing domain contracts.
- **Alternatives considered**: SQLModel (couples ORM and Pydantic) — rejected: it blurs the domain/infra boundary the engine depends on.

## R6. Secrets — HashiCorp Vault, fail-closed

- **Decision**: `hvac` client in `infra/vault.py`. At lifespan startup, the app reads required secrets from Vault (KV v2). If Vault is unreachable or a required secret is missing → raise, app refuses to boot. Dev Vault runs in `-dev` mode in compose with a known root token; an init step writes placeholder secrets.
- **Rationale**: Mandate + constitution. "Fail closed" is a graded property. `os.getenv` is confined to `config.py`; secret *values* come only from Vault.
- **Alternatives considered**: env-only secrets (no central rotation, easy to leak) — rejected by mandate. Cloud secret managers — out of scope for local Phase 0.

## R7. Cache & background work — Redis + RQ

- **Decision**: `redis` 7 image; `redis-py` (async) for cache/session; `rq` for the worker. Worker entrypoint `rq worker keel` against the same Redis.
- **Rationale**: Mandate. RQ is simple, Redis-backed, fits the bounded job set (capacity sync, waitlist, outbox publisher, alerts, replan).
- **Alternatives considered**: Celery (heavier, more moving parts) — rejected for simplicity (constitution: simplicity is a feature).

## R8. Object store — MinIO via boto3

- **Decision**: MinIO (S3-compatible) for catalog text, model artifacts, eval reports. `boto3` S3 client in `infra/storage.py`. Buckets created idempotently at startup/seed.
- **Rationale**: Mandate. S3 API is ubiquitous; MLflow also uses MinIO as its artifact store. boto3 is the standard S3 client.
- **Alternatives considered**: local filesystem (not production-like, no S3 API for MLflow) — rejected.

## R9. Experiment registry — MLflow

- **Decision**: MLflow tracking server in compose, backed by Postgres (backend store) and MinIO (artifact store). Phase 0 only needs the server healthy and UI reachable; model logging/promotion is Phase 1+.
- **Rationale**: Mandate. Registry is the source of truth for model artifacts and staging→production promotion.
- **Alternatives considered**: Weights & Biases (SaaS, cost) — rejected by mandate.

## R10. Tracing & logging — OpenTelemetry + LangSmith + structlog

- **Decision**: OTel SDK initialized in lifespan; FastAPI auto-instrumentation; OTLP exporter (degrades gracefully if no collector configured). LangSmith env wired for later LLM tracing. `structlog` emits JSON logs with `event/level/timestamp/service/request_id/trace_id/tenant_id`.
- **Rationale**: Engineering rules §14/§16 + constitution ("traces and logs are tenant-scoped"). Graceful degradation so missing collector never blocks boot.
- **Alternatives considered**: stdlib logging only (no structure), Jaeger-specific client (vendor lock) — OTel is vendor-neutral.
- **Completed later (D-R-014)**: the OTLP exporter now has a target — an all-in-one **Jaeger** backend in compose (UI `:16686`), receiving **vendor-neutral OTLP** (no Jaeger client — the lock-in concern above still holds). Added `instrument_libraries()` (SQLAlchemy + Redis + **httpx**, so DB/cache and the outbound LLM/model-server calls trace), agent spans (`agent.turn` / `agent.llm` / `agent.tool.*` with input/output previews, in `agent/tracing.py`), and the same tracing init in the **worker** entrypoint. `OTEL_EXPORTER_OTLP_ENDPOINT` defaults to `http://jaeger:4317`; empty still degrades gracefully. Previews are capped and `redact()`-ed so spans carry no PII/transcript.

## R11. CI — GitHub Actions

- **Decision**: `.github/workflows/ci.yml` runs: `uv sync` (both projects) → `ruff check` + `ruff format --check` → `mypy` → build all three images → `docker compose up` smoke test hitting `/healthz`. Green for the delivered scaffold.
- **Rationale**: Mandate (CI from Day 1). Matrix can lint/type both packages.
- **Alternatives considered**: none material; GitHub Actions is the project default.

## R12. Migration authoring — explicit ops + raw SQL for RLS

- **Decision**: Baseline migration uses Alembic `op.create_table` for tables and `op.execute` for the `pgvector` extension and all RLS statements (`ENABLE/FORCE ROW LEVEL SECURITY`, `CREATE POLICY`). Hand-authored (not autogenerate) because autogenerate cannot emit RLS or extensions.
- **Rationale**: RLS is the non-negotiable isolation layer; it must be explicit, reviewable, and reversible in the migration. `downgrade()` drops policies and tables in reverse dependency order.
- **Alternatives considered**: autogenerate then hand-patch — rejected: less clear provenance for the security-critical RLS statements.

## Open questions / deferred

- **None blocking Phase 0.** LangGraph/LangChain versions are chosen in Phase 2. Specific OTLP collector endpoint is environment config, defaulted off locally. Model artifact SHA pinning is Phase 1 (model cards).
