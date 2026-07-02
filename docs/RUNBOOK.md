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

### Jaeger UI — http://localhost:16686

Distributed tracing (D-R-014). `api` and `worker` export OTLP to the in-stack
Jaeger (`OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317`, default). Pick service
`keel-api` (or `keel-worker`) → a chat turn reads top-down: FastAPI request →
`agent.turn` → `agent.llm` (the model's tool-call decisions) → `agent.tool.*`
(tool inputs/outputs) → SQLAlchemy / Redis / **httpx** child spans (DB, cache, and
the LLM/model-server calls). Set `OTEL_EXPORTER_OTLP_ENDPOINT=` empty to disable
(spans still created, no export). Traces are in-memory — restarting Jaeger clears them.

### Northane Portal — http://localhost:3001
### Summit Portal — http://localhost:3002
### Keel Console — http://localhost:8000/keel/
The admin + operator console is one role-based SPA served at `/keel` (a
`tenant_admin` JWT lands on the admin views; a `platform_operator` JWT lands on the
operator views). Log in via `POST /auth/login` to get the JWT. The widget iframe is
served at `/widget/`; `widget.js` (the loader) at `/widget.js`.

### Where notifications go
After an approved write, the durable notification path is **outbox → worker**: the
`outbox_publisher` job (per-tenant, RLS-scoped) enqueues each unprocessed row and
`send_outbox_event` delivers it, marking `processed=true`.

**Email (G4).** Email is **ON** for **Keel-originated actions only** (registration,
waitlist, seat-fill, petition, graduation application, major-change, escalation).
With SMTP disabled (default) the send is **simulated** — logged as
`email.simulated_send` to the demo inbox `mousaelisar@gmail.com` (we have no real
per-student mailboxes). To send for real, set `KEEL_SMTP_ENABLED=true` + host. The
student also sees an **inline widget confirmation** at action time.

**No Keel email for SIS actions.** A registrar approve/reject is an **SIS-domain**
outcome, not a Keel action — the portal writes a `request.approved` /
`request.rejected` outbox + `audit_log` row, and the worker **skips** emailing it
(`worker.email.skipped_non_keel`). Keel never notifies on another system's decision.

---

## Demo credentials (demo-only — never use in production)

> All passwords are configurable via env vars (`KEEL_OPERATOR_PASSWORD`, `KEEL_ADMIN_PASSWORD`, `KEEL_PORTAL_PASSWORD`). The defaults below are the demo values seeded by `seed.py`.

### Platform Operator (POST /auth/login → Keel API, then use Platform Console)
| Email | Password |
|---|---|
| `operator@keel.platform` | `123` |

### Tenant Admins (POST /auth/login → Keel API, then use Admin Console)
| Email | Password | Tenant |
|---|---|---|
| `admin@northane.edu` | `123` | Northane University |
| `admin@summit.edu` | `123` | Summit College |

### Portal Students (email + password on portal login page)
| Email | Password | Portal | Student |
|---|---|---|---|
| `alisar@northane.edu` | `123` | Northane (:3001) | Alisar Hadid |
| `omar@northane.edu` | `123` | Northane (:3001) | Omar Khalil |
| `lina@northane.edu` | `123` | Northane (:3001) | Lina Saab (at-risk) |
| `maya@summit.edu` | `123` | Summit (:3002) | Maya Haddad |
| `jad@summit.edu` | `123` | Summit (:3002) | Jad Nasser (57 cr) |
| `sara@summit.edu` | `123` | Summit (:3002) | Sara Khoury (hold) |

### Portal Registrars
| Email | Password | Portal |
|---|---|---|
| `registrar@northane.edu` | `123` | Northane (:3001) |
| `registrar@summit.edu` | `123` | Summit (:3002) |

---

## Cross-tenant demo flow

1. Log into Northane portal (`http://localhost:3001`) as `alisar@northane.edu` → My Schedule → open widget → plan → approve → enroll → `via Keel` badge appears.
2. Log into Summit portal (`http://localhost:3002`) as `maya@summit.edu` (different origin) → only Summit data is visible.
3. **Prove isolation:** replay a Northane widget token against Summit → 403; use Summit-origin mint for Northane tenant → 403.
4. **Operator suspends Summit:** log into Platform Console as `operator@keel.platform` → Tenants → Suspend Summit. Summit students still log into the portal and see My Schedule (SIS stays up), but the widget returns 403 and shows "unavailable". Northane is fully unaffected. Unsuspend restores the widget.

---

### Not browser-accessible (internal Docker network only)

| Service | How to access |
|---|---|
| Postgres (port 5432) | pgAdmin or `psql` — connect as `postgres` / `POSTGRES_PASSWORD` from `.env` |
| Redis | No host port — internal only |

---

## Exposing MLflow to Colab (tunneling)

Colab runs remotely and cannot reach `http://mlflow:5000` (a Docker-internal hostname) or even `http://localhost:5001` directly. You need a tunnel that maps a public URL to `localhost:5001` on your machine.

### Option A — ngrok (recommended)

1. Install: https://ngrok.com/download or `brew install ngrok`
2. Sign up (free) and run once: `ngrok config add-authtoken <your-token>`
3. Start the tunnel (run this before opening Colab):
   ```bash
   ngrok http 5001
   ```
4. Copy the `https://xxxx.ngrok-free.app` URL from the terminal output.
5. In your Colab notebook, set:
   ```python
   import os
   os.environ["MLFLOW_TRACKING_URI"] = "https://xxxx.ngrok-free.app"
   ```
   Replace with your actual ngrok URL. The URL changes each session unless you have a paid ngrok account with a reserved domain.

### Option B — cloudflared (no account required)

```bash
brew install cloudflare/cloudflare/cloudflared   # macOS
cloudflared tunnel --url http://localhost:5001
```

Copy the `https://xxxx.trycloudflare.com` URL it prints and use it the same way as Option A.

### Option C — VS Code port forwarding

If you already have VS Code open: `View → Ports → Forward a Port → 5001`. VS Code gives you an `https://` URL you can paste into Colab.

### Important notes

- Keep the stack (`docker compose up -d`) AND the tunnel running simultaneously while training.
- `--serve-artifacts` means Colab uploads model artifacts *through* the MLflow server — no MinIO credentials are needed in Colab. Just the tracking URI.
- The tunnel URL must be `https` (not `http`). MLflow's Colab client requires it.
- After training finishes, close the tunnel; MLflow continues running locally.
