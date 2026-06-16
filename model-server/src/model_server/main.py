"""Model-server FastAPI app — lean joblib/onnxruntime serving.

Startup sequence (fail-closed):
1. Locate ``model_a.joblib`` (intent, TF-IDF+LogReg) and ``grad_risk.onnx``
   (graduation-risk, HistGradientBoosting exported to ONNX).
2. Verify SHA-256 of each artifact — refuse to boot on mismatch or missing file.
3. Load into memory: joblib for intent, onnxruntime.InferenceSession for grad_risk.
4. Expose ``/intent``, ``/grad_risk``, ``/healthz``.

No torch. grad_risk uses ONNX (ABI-stable across sklearn versions); intent uses
joblib since it is a TF-IDF+LogReg pipeline with no compatible ONNX export yet.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import onnxruntime as ort
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


def _verify(path: Path, expected: str, name: str) -> None:
    """Verify SHA-256 of an artifact file. Raises RuntimeError on mismatch."""
    if not path.exists():
        raise RuntimeError(f"[{name}] artifact not found: {path}")
    actual = _sha256(path)
    if actual != expected:
        raise RuntimeError(
            f"[{name}] SHA-256 mismatch — "
            f"expected {expected}, got {actual}. "
            "Re-run the artifact sync or update the SHA pin in config."
        )


def _load_joblib(path: Path, expected: str, name: str) -> Any:
    """Verify then load a joblib artifact."""
    _verify(path, expected, name)
    return joblib.load(path)


def _load_onnx(path: Path, expected: str, name: str) -> ort.InferenceSession:
    """Verify then load an ONNX artifact via onnxruntime."""
    _verify(path, expected, name)
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


# Module-level store — populated during lifespan, cleared on shutdown.
_models: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = get_settings()

    intent_path = Path(cfg.intent_artifacts_dir) / "model_a.joblib"
    grad_risk_path = Path(cfg.grad_risk_artifacts_dir) / "grad_risk.onnx"

    _models["intent"] = _load_joblib(intent_path, cfg.intent_model_sha256, "intent")
    _models["grad_risk"] = _load_onnx(grad_risk_path, cfg.grad_risk_model_sha256, "grad_risk")

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
    score: float  # P(at_risk) ∈ [0, 1]
    label: str  # "at_risk" | "on_track"


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
    session: ort.InferenceSession | None = _models.get("grad_risk")
    if session is None:
        raise HTTPException(status_code=503, detail="grad_risk model not loaded")
    if len(req.features) != 9:
        raise HTTPException(
            status_code=422,
            detail=f"expected 9 features (FEATURE_ORDER), got {len(req.features)}",
        )
    vec = np.array(req.features, dtype=np.float32).reshape(1, -1)
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: vec})
    # sklearn→ONNX ZipMap: outputs[1] is list[dict[int, float]] per sample.
    # {0: P(on_track), 1: P(at_risk)}
    prob_dict: dict[int, float] = outputs[1][0]
    score = float(prob_dict.get(1, 0.0))
    return GradRiskResponse(score=score, label="at_risk" if score >= 0.5 else "on_track")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "model-server",
        "version": __version__,
        "models": sorted(_models.keys()),
    }
