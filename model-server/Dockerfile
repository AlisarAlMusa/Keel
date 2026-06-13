# Keel model-server image. Build context = ./model-server (its OWN context).
# Lean: onnxruntime/joblib only — no torch, no backend dependencies.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_HTTP_TIMEOUT=300

# onnxruntime/numpy wheels are large; 300s avoids download timeouts on slow links.

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 9000

# Python-based healthcheck (no curl/apt dependency; venv python is on PATH).
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9000/healthz').getcode()==200 else 1)"]

CMD ["uvicorn", "model_server.main:app", "--host", "0.0.0.0", "--port", "9000"]
