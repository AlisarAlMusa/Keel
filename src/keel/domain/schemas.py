"""Cross-cutting Pydantic schemas shared across api, services, agent, and infra layers.

Keep this module import-clean: no I/O, no framework imports, no engine imports.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ToolError(BaseModel):
    """Structured error returned by any agent tool on failure."""

    error: str
    retryable: bool
    category: Literal["validation", "engine", "model", "external", "auth"]


class IntentPrediction(BaseModel):
    """Raw output of the intent classifier (model-server /intent)."""

    label: str
    confidence: float


class GradRiskPrediction(BaseModel):
    """Raw output of the grad-risk model (model-server /grad_risk)."""

    score: float  # probability of at-risk ∈ [0, 1]
    label: Literal["at_risk", "on_track"]


class RouterResult(BaseModel):
    """Full routing decision after applying the confidence threshold."""

    label: str
    confidence: float
    route_to_agent: bool


class StudentPreference(BaseModel):
    """Closed schema for per-student UI and advising preferences.

    Closed intentionally — new preference fields require an explicit migration,
    not a free-form dict that could silently grow unbounded.
    """

    response_style: Literal["brief", "detailed"] = "detailed"
    language: str = "en"
    difficulty_preference: Literal["light", "medium", "heavy", "any"] = "any"
    career_interest: str | None = None


class RagResult(BaseModel):
    """One ranked chunk returned by the RAG pipeline."""

    chunk_id: str
    source: str
    type: str       # "course" | "policy"
    content: str
    score: float    # rerank relevance score or RRF score
    code: str | None = None       # course code (if type == "course")
    doc: str | None = None        # policy doc name (if type == "policy")
    section: str | None = None    # policy section heading
    lang: str = "en"


class ContextEnvelope(BaseModel):
    """Fully-typed wrapper passed into every agent invocation."""

    tenant_id: str
    student_id: str
    session_id: str
    request_id: str
    message: str
    preferences: StudentPreference = StudentPreference()
