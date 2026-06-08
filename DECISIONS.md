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

### D-008 — LangGraph/LangChain not installed in Phase 0

**Decision.** Agent dependencies (LangGraph/LangChain) are deferred to Phase 2,
when the bounded agent is built — they are not in the Phase 0 backend lockfile.

**Rationale.** Keep the foundation image lean; install heavy agent deps the day
the agent lands. The `agent/` package is scaffolded empty so the code has a home.
