"""LLM factory — returns a configured ``ChatGoogleGenerativeAI`` instance.

Usage (in lifespan, after Vault secrets are loaded):

    from keel.infra import llm as llm_infra
    app.state.llm_agent = llm_infra.get_llm("agent", api_key=secrets["gemini_api_key"])
    app.state.llm_lite  = llm_infra.get_llm("lite",  api_key=secrets["gemini_api_key"])

Roles
-----
agent  — full model (plan proposal, repair, advise, RAG synthesis)
lite   — fast model (chitchat, out_of_scope; caller enforces 50-token cap)
judge  — full model (RAG relevance scoring, re-ranking rationale)
"""

from __future__ import annotations

from typing import Literal

from langchain_google_genai import ChatGoogleGenerativeAI

from keel.config import get_settings

Role = Literal["agent", "lite", "judge"]

_TEMPERATURE: dict[str, float] = {
    "agent": 0.0,  # deterministic — plan repair must be consistent
    "lite": 0.7,  # varied — chitchat benefits from natural phrasing
    "judge": 0.0,  # deterministic — relevance scoring must be repeatable
}


def get_llm(role: Role, *, api_key: str) -> ChatGoogleGenerativeAI:
    """Return a pinned, role-appropriate ``ChatGoogleGenerativeAI`` instance.

    The ``api_key`` must come from Vault (never from env directly).
    Model names are read from ``Settings`` so they're tuneable without a redeploy.
    """
    cfg = get_settings()
    model_name = cfg.gemini_lite_model if role == "lite" else cfg.gemini_model
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
        temperature=_TEMPERATURE[role],
    )
