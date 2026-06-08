# Contract: Health Endpoints

Both the backend API and the model-server expose health endpoints used by Docker Compose healthchecks and the CI smoke test. These are the only HTTP contracts in Phase 0 (the app is otherwise empty-but-healthy).

## Backend API (`keel`, port 8000)

### `GET /healthz` — liveness
- **Purpose**: process is up and serving.
- **Auth**: none.
- **200 OK**:
  ```json
  { "status": "ok", "service": "keel-api", "version": "<app-version>" }
  ```
- **Never** touches DB/Vault — pure liveness so it stays green during dependency hiccups.

### `GET /readyz` — readiness
- **Purpose**: app booted successfully *including* the Vault gate, and core dependencies are reachable.
- **Auth**: none.
- **200 OK** (all dependencies reachable):
  ```json
  {
    "status": "ready",
    "checks": { "vault": "ok", "db": "ok", "redis": "ok" }
  }
  ```
- **503 Service Unavailable** (any check fails):
  ```json
  {
    "status": "not_ready",
    "checks": { "vault": "ok", "db": "error", "redis": "ok" }
  }
  ```
- **Boot semantics**: if Vault was unreachable at startup, the app **does not start** (fail closed), so `/readyz` is unreachable rather than returning a Vault error — that is the intended behavior (FR-012).

## Model-server (`model-server`, port 9000)

### `GET /healthz` — liveness + artifact integrity
- **Purpose**: process up; in later phases also asserts loaded model artifact SHA-256 matches the model card.
- **Auth**: none.
- **200 OK** (Phase 0 — no models loaded yet):
  ```json
  { "status": "ok", "service": "model-server", "models": [] }
  ```
- **503**: (later phases) artifact SHA mismatch → server refuses readiness.

## Compose healthcheck mapping

| service | healthcheck |
|---------|-------------|
| api | `curl -f http://localhost:8000/healthz` |
| model-server | `curl -f http://localhost:9000/healthz` |
| db | `pg_isready` |
| redis | `redis-cli ping` |
| minio | `curl -f http://localhost:9000/minio/health/live` (MinIO's own, internal port) |
| vault | `vault status` (dev mode) |
| mlflow | `curl -f http://localhost:5000/health` |
| worker | process-based (no HTTP); depends_on db+redis healthy |

## CI smoke acceptance
- After `docker compose up -d`, poll `GET /healthz` (api) and `GET /healthz` (model-server) until 200 or timeout; assert both 200. Assert `db`, `redis`, `vault`, `minio`, `mlflow` reach healthy via `docker compose ps`.
