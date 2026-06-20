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

### Northane Portal — http://localhost:3001
### Summit Portal — http://localhost:3002
### Admin Dashboard — http://localhost:8000/admin-ui/
### Platform Console — http://localhost:8000/platform-ui/

---

## Demo credentials (demo-only — never use in production)

> All passwords are configurable via env vars (`KEEL_OPERATOR_PASSWORD`, `KEEL_ADMIN_PASSWORD`, `KEEL_PORTAL_PASSWORD`). The defaults below are the demo values seeded by `seed.py`.

### Platform Operator (POST /auth/login → Keel API, then use Platform Console)
| Email | Password |
|---|---|
| `operator@keel.platform` | `keel-operator-demo` |

### Tenant Admins (POST /auth/login → Keel API, then use Admin Console)
| Email | Password | Tenant |
|---|---|---|
| `admin@northane.edu` | `keel-admin-demo` | Northane University |
| `admin@summit.edu` | `keel-admin-demo` | Summit College |

### Portal Students (email + password on portal login page)
| Email | Password | Portal | Notes |
|---|---|---|---|
| `alisar@northane.edu` | `keel-portal-demo` | Northane (:3001) | Maps to Alex Morgan |
| `omar@northane.edu` | `keel-portal-demo` | Northane (:3001) | Maps to Jordan Lee |
| `lina@northane.edu` | `keel-portal-demo` | Northane (:3001) | Maps to Riley Chen (at-risk) |
| `maya@summit.edu` | `keel-portal-demo` | Summit (:3002) | Maps to Taylor Brooks |
| `jad@summit.edu` | `keel-portal-demo` | Summit (:3002) | Maps to Morgan Patel |
| `sara@summit.edu` | `keel-portal-demo` | Summit (:3002) | Maps to Casey Wu (hold) |

### Portal Registrars
| Email | Password | Portal |
|---|---|---|
| `registrar@northane.edu` | `keel-portal-demo` | Northane (:3001) |
| `registrar@summit.edu` | `keel-portal-demo` | Summit (:3002) |

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
