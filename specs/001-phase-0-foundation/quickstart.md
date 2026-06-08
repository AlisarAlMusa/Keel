# Quickstart: Phase 0 — Foundation

How to bring up and verify the Keel foundation from a fresh clone. This is the developer-facing path that User Story 1 promises and the CI smoke test automates.

## Prerequisites
- Docker + Docker Compose v2
- `uv` (for local dev / running migrations outside containers); host Python may be any 3.x — `uv` provisions 3.12.

## 1. Configure
```bash
cp .env.example .env
# defaults work for local; no edits needed to boot the stack
```

## 2. Bring up the stack
```bash
docker compose up -d --build
```
Brings up: **api, worker, model-server, db (Postgres+pgvector), redis, minio, vault, mlflow**.

Watch them reach healthy:
```bash
docker compose ps
```

## 3. Vault bootstrap (automatic)
The `vault` service runs in dev mode; a one-shot init step writes placeholder secrets to `secret/keel/app`. The `api` and `worker` will only start after secrets are present — if Vault is down, they fail closed (by design).

## 4. Apply migrations
```bash
docker compose exec api uv run alembic upgrade head
```
Creates all 16 tables + `vector`/`pgcrypto` extensions + RLS policies on the 15 tenant-owned tables.

## 5. Seed development data
```bash
docker compose exec api uv run python -m scripts.seed
```
Creates 2 tenants, each with ≥20 courses (prereq chains), sections, program requirements, 2 transcripts; uploads catalog text to MinIO.

## 6. Verify
```bash
# API health
curl -s localhost:8000/healthz        # {"status":"ok",...}
curl -s localhost:8000/readyz         # {"status":"ready","checks":{...}}

# Model-server health
curl -s localhost:9000/healthz        # {"status":"ok","service":"model-server",...}

# Tables + RLS (expect 16 tables, 15 policies)
docker compose exec db psql -U keel_app -d keel -c "\dt"
docker compose exec db psql -U keel_app -d keel -c "SELECT count(*) FROM pg_policies WHERE policyname='tenant_isolation';"

# MLflow UI
open http://localhost:5001

# MinIO console
open http://localhost:9001
```

## 7. Fail-closed check (User Story 2)
```bash
docker compose stop vault
docker compose restart api          # api refuses to boot; logs explain why
docker compose start vault
docker compose restart api          # api boots
```

## Local dev (without full stack)
```bash
uv sync                              # backend env
uv run ruff check . && uv run mypy src
uv run pytest

cd model-server && uv sync           # model-server env (isolated)
```

## Teardown
```bash
docker compose down                  # keep volumes (data persists)
docker compose down -v               # wipe volumes (fresh start)
```

## Verifies which requirements
- SC-001 (one-command healthy stack), SC-002 (fail-closed), SC-003 (16 tables + RLS), SC-004 (seed counts + MinIO), SC-007 (restart durability) — all exercised by the steps above and automated in CI smoke.
