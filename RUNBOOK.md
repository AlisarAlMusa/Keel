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
