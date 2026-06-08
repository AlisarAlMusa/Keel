# Contract: Environment & Configuration

Every value the stack needs to run. Documented in `.env.example`. **Secret *values* are resolved from Vault at startup, not from env** — env carries only non-secret config and the Vault *coordinates* (address + token) needed to reach Vault. `config.py` (pydantic-settings, `extra="forbid"`) is the only place `os.getenv` is read.

## Non-secret config (env)

| key | example | purpose |
|-----|---------|---------|
| `KEEL_ENV` | `local` | environment name (local/ci/prod) |
| `KEEL_LOG_LEVEL` | `INFO` | log level |
| `KEEL_API_PORT` | `8000` | api bind port |
| `DATABASE_URL` | `postgresql+asyncpg://keel_app:...@db:5432/keel` | async DSN (password is a placeholder locally; prod password comes from Vault) |
| `REDIS_URL` | `redis://redis:6379/0` | cache + RQ broker |
| `MINIO_ENDPOINT` | `http://minio:9000` | object store endpoint |
| `MINIO_BUCKET` | `keel-artifacts` | default bucket |
| `MLFLOW_TRACKING_URI` | `http://mlflow:5000` | experiment registry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `` (empty) | OTLP collector; empty → tracing degrades gracefully |
| `OTEL_SERVICE_NAME` | `keel-api` | trace service name |
| `LANGSMITH_TRACING` | `false` | enable LangSmith (later phases) |
| `MODEL_SERVER_URL` | `http://model-server:9000` | backend → model-server client |

## Vault coordinates (env — how to reach Vault)

| key | example | purpose |
|-----|---------|---------|
| `VAULT_ADDR` | `http://vault:8200` | Vault address |
| `VAULT_TOKEN` | `keel-dev-root-token` | dev-mode token (local only); prod uses a real auth method |
| `VAULT_KV_MOUNT` | `secret` | KV v2 mount |
| `VAULT_SECRET_PATH` | `keel/app` | path holding app secrets |

## Secrets (resolved from Vault, NOT in env)

Read from `VAULT_KV_MOUNT/VAULT_SECRET_PATH` at startup. Missing any required key → **app refuses to boot**.

| secret key | purpose |
|------------|---------|
| `db_password` | DB password (merged into the DSN at runtime) |
| `minio_access_key` | object store access key |
| `minio_secret_key` | object store secret key |
| `jwt_signing_key` | admin JWT signing (later phases) |
| `widget_token_secret` | widget token HMAC (later phases) |
| `langsmith_api_key` | LangSmith key (optional; only required if `LANGSMITH_TRACING=true`) |

## Startup contract (fail-closed)

1. Load env via pydantic-settings (`extra="forbid"` → unknown keys error).
2. Connect to Vault using `VAULT_ADDR`/`VAULT_TOKEN`.
3. Read all **required** secret keys from `VAULT_SECRET_PATH`.
4. If Vault unreachable, errors, times out, or a required secret is missing → log a clear non-sensitive error and **raise** (app does not start).
5. On success, construct singletons (DB engine, Redis, MinIO client, model-server client, tracer) and proceed to serve.

## Acceptance
- `.env.example` lists every key above with a placeholder and a one-line comment.
- No secret value is committed anywhere in the repo.
- Booting with Vault down fails closed (User Story 2 / FR-012).
