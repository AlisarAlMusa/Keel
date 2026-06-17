"""LLM factory — returns a model chain with automatic fallbacks.

Primary model is tried first; if it raises a rate-limit (429) or transient
error (503), LangChain's ``with_fallbacks`` transparently retries the next
model in the chain.  This prevents a Gemini free-tier quota exhaustion from
killing the whole agent.

Fallback chain (configured in Settings):
  gemini-2.5-flash  →  gemini-2.0-flash  →  gemini-1.5-flash

Usage (in lifespan, after Vault secrets are loaded):

    from keel.infra import llm as llm_infra
    app.state.llm_agent = llm_infra.get_llm("agent", api_key=secrets["gemini_api_key"])
    app.state.llm_lite  = llm_infra.get_llm("lite",  api_key=secrets["gemini_api_key"])

Roles
-----
agent  — full model with fallback chain (plan proposal, repair, advise, RAG synthesis)
lite   — fast model, no fallback (chitchat; caller enforces 50-token cap)
judge  — full model with fallback chain (RAG relevance scoring)
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI

from keel.config import get_settings
from keel.logging import get_logger

_log = get_logger(__name__)

Role = Literal["agent", "lite", "judge"]

_TEMPERATURE: dict[str, float] = {
    "agent": 0.0,
    "lite": 0.7,
    "judge": 0.0,
}

# Exceptions that should trigger a fallback to the next model.
# We catch all LangChain / Google API transient errors.
_FALLBACK_EXCEPTIONS: tuple[type[Exception], ...] = (Exception,)


def _make_model(model_name: str, api_key: str, temperature: float) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
        temperature=temperature,
    )


def get_llm(role: Role, *, api_key: str) -> Runnable[Any, Any]:
    """Return a role-appropriate LLM, wrapped in a fallback chain for agent/judge roles.

    The ``api_key`` must come from Vault (never from env directly).
    Model names are read from ``Settings`` so they're tuneable without a redeploy.
    """
    cfg = get_settings()
    temp = _TEMPERATURE[role]

    if role == "lite":
        primary_lite = _make_model(cfg.gemini_lite_model, api_key, temp)
        fallback_lite = _make_model(cfg.gemini_fallback_models[0], api_key, temp)
        chain = primary_lite.with_fallbacks(
            [fallback_lite],
            exceptions_to_handle=_FALLBACK_EXCEPTIONS,
        )
        _log.info(
            "llm.chain_built",
            role=role,
            primary=cfg.gemini_lite_model,
            fallbacks=[cfg.gemini_fallback_models[0]],
        )
        return chain

    # agent and judge: build primary + fallback chain
    primary = _make_model(cfg.gemini_model, api_key, temp)
    fallbacks = [_make_model(m, api_key, temp) for m in cfg.gemini_fallback_models]

    if not fallbacks:
        return primary

    chain = primary.with_fallbacks(
        fallbacks,
        exceptions_to_handle=_FALLBACK_EXCEPTIONS,
    )
    _log.info(
        "llm.chain_built",
        role=role,
        primary=cfg.gemini_model,
        fallbacks=cfg.gemini_fallback_models,
    )
    return chain
