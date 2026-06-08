"""Model-server FastAPI app — lean ONNX/joblib serving.

Phase 0: empty-but-healthy. Exposes ``/healthz`` only. In later phases this
service loads exported artifacts (intent classifier, graduation-risk), verifies
their SHA-256 against the model card, and refuses to boot on mismatch.

No torch, no SQLAlchemy, no LangGraph — this package is isolated by design.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

__version__ = "0.1.0"

app = FastAPI(title="Keel model-server", version=__version__)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness. ``models`` is empty until artifacts are loaded (later phases)."""
    return {"status": "ok", "service": "model-server", "version": __version__, "models": []}
