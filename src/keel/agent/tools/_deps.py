"""Shared dependency container for all agent tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cohere
from langchain_google_genai import ChatGoogleGenerativeAI

from keel.config import Settings
from keel.domain.models import Term


@dataclass
class AgentDeps:
    """All injectable dependencies the tools need."""

    session_factory: Any  # async_sessionmaker — tools open their own sessions per call
    cohere_client: cohere.AsyncClientV2
    llm_agent: ChatGoogleGenerativeAI
    settings: Settings
    current_term: Term = Term.FALL
    current_year: int = 2025
    # Phase 3: model-server client for predict_risk.
    model_client: Any = field(default=None)  # ModelClient | None
