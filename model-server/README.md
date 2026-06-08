# Keel model-server

Lean model-serving microservice. Serves exported ONNX/joblib artifacts (intent
classifier, graduation-risk) over HTTP using `onnxruntime`/`joblib` only.

**Hard constraint:** no `torch`, no SQLAlchemy, no LangGraph — this package is
isolated from the backend's dependency tree by design (see `DECISIONS.md`).

Phase 0 status: empty-but-healthy (`GET /healthz` only). Model loading and
SHA-256 artifact pinning arrive in later phases.

Run locally:

```bash
cd model-server
uv sync
uv run uvicorn model_server.main:app --port 9000
```
