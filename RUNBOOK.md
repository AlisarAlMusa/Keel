# Keel Runbook

## Local service URLs

Start the stack: `docker compose up -d --build`

### Keel API — http://localhost:8000

| URL | Purpose |
|---|---|
| http://localhost:8000/healthz | Liveness — process is up and serving |
| http://localhost:8000/readyz | Readiness — DB + Redis reachable; returns per-check status |
| http://localhost:8000/docs | Swagger UI (auto-generated) |
| http://localhost:8000/redoc | ReDoc API reference |

### Model Server — http://localhost:9000

| URL | Purpose |
|---|---|
| http://localhost:9000/healthz | Liveness — returns `{"status": "ok", "models": []}` until Phase 2 loads artifacts |

### Vault UI — http://localhost:8200

Login with token: `VAULT_TOKEN` from `.env` (default: `keel-dev-root-token`).
Secrets live at: `secret/keel/app`.

### MinIO Console — http://localhost:9001

Login with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from `.env`.
Bucket: `keel-artifacts`.

### MLflow UI — http://localhost:5001

Experiment tracking. Port is 5001 on host (macOS AirPlay holds 5000).

---

### Not browser-accessible (internal Docker network only)

| Service | How to access |
|---|---|
| Postgres (port 5432) | pgAdmin or `psql` — connect as `postgres` / `POSTGRES_PASSWORD` from `.env` |
| Redis | No host port — internal only |
