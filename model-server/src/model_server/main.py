"""Model-server FastAPI app — lean joblib/sklearn serving.

Startup sequence (fail-closed):
1. Locate ``model_a.joblib`` (intent) and ``grad_risk.joblib`` (graduation-risk).
2. Verify SHA-256 of each artifact — refuse to boot on mismatch or missing file.
3. Load models into memory via joblib.
4. Expose ``/intent``, ``/grad_risk``, ``/healthz``.

No torch, no ONNX, no SQLAlchemy, no LangGraph — isolated by design.
Domain logic lives in ``keel.domain``; this service is pure inference.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_server.config import get_settings

__version__ = "0.2.0"


# ---------------------------------------------------------------------------
# Artifact loading helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_and_verify(path: Path, expected: str, name: str) -> Any:
    """Load a joblib artifact after verifying its SHA-256.  Raises on mismatch."""
    if not path.exists():
        raise RuntimeError(f"[{name}] artifact not found: {path}")
    actual = _sha256(path)
    if actual != expected:
        raise RuntimeError(
            f"[{name}] SHA-256 mismatch — "
            f"expected {expected}, got {actual}. "
            "Re-run the artifact sync or update the SHA pin in config."
        )
    return joblib.load(path)


# Module-level store — populated during lifespan, cleared on shutdown.
_models: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = get_settings()

    intent_path = Path(cfg.intent_artifacts_dir) / "model_a.joblib"
    grad_risk_path = Path(cfg.grad_risk_artifacts_dir) / "grad_risk.joblib"

    _models["intent"] = _load_and_verify(intent_path, cfg.intent_model_sha256, "intent")
    _models["grad_risk"] = _load_and_verify(
        grad_risk_path, cfg.grad_risk_model_sha256, "grad_risk"
    )

    yield

    _models.clear()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Keel model-server", version=__version__, lifespan=lifespan)


# --- Schemas ----------------------------------------------------------------


class IntentRequest(BaseModel):
    text: str


class IntentResponse(BaseModel):
    label: str
    confidence: float


class GradRiskRequest(BaseModel):
    features: list[float]


class GradRiskResponse(BaseModel):
    score: float   # P(at_risk) ∈ [0, 1]
    label: str     # "at_risk" | "on_track"


# --- Endpoints --------------------------------------------------------------


@app.post("/intent", response_model=IntentResponse)
async def predict_intent(req: IntentRequest) -> IntentResponse:
    model = _models.get("intent")
    if model is None:
        raise HTTPException(status_code=503, detail="intent model not loaded")
    # TF-IDF + LogisticRegression pipeline: input is raw text.
    # Use classes_[argmax] — NOT label_map.id2label (see intent model card).
    proba: np.ndarray = model.predict_proba([req.text])[0]
    idx = int(proba.argmax())
    return IntentResponse(label=str(model.classes_[idx]), confidence=float(proba[idx]))


@app.post("/grad_risk", response_model=GradRiskResponse)
async def predict_grad_risk(req: GradRiskRequest) -> GradRiskResponse:
    model = _models.get("grad_risk")
    if model is None:
        raise HTTPException(status_code=503, detail="grad_risk model not loaded")
    if len(req.features) != 9:
        raise HTTPException(
            status_code=422,
            detail=f"expected 9 features (FEATURE_ORDER), got {len(req.features)}",
        )
    vec = np.array(req.features, dtype=np.float64).reshape(1, -1)
    proba: np.ndarray = model.predict_proba(vec)[0]
    # classes_ = [0, 1] when trained on int labels; 1 = at_risk.
    class_list: list[int] = list(model.classes_)
    try:
        at_risk_idx = class_list.index(1)
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="unexpected class labels in grad_risk model"
        ) from exc
    score = float(proba[at_risk_idx])
    return GradRiskResponse(score=score, label="at_risk" if score >= 0.5 else "on_track")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "model-server",
        "version": __version__,
        "models": sorted(_models.keys()),
    }
