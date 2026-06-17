"""Classifier router — the deterministic supervisor between easy and hard turns.

Architecture (DECISIONS.md D-P2-001):
  1. Call model-server /intent → proba vector.
  2. argmax → route label, max_prob → confidence.
  3. If conf ≥ FALLBACK_THRESHOLD (from router_config.json): dispatch to FLOWS[route].
     Else → agent.run() (agent re-derives intent from context, not the classifier label).
  4. Classifier error → agent (fail-safe).

15 labels (all wired now — no router changes needed as Phase 3+ flows land):
  REAL FLOWS (this phase):
    plan         → agent (propose_plan flow — T5)
    advise       → agent (RAG advise flow — T5)
    audit        → agent (engine audit flow — T5)
    chitchat     → LLM-direct lite (50-token cap)
    out_of_scope → LLM-direct lite (50-token cap)

  STUBS (replaced phase by phase):
    whatif / predict / register / waitlist / plans_manage /
    my_info / grad_apply / major_change / petition / escalate
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from keel.domain.schemas import ContextEnvelope, RouterResult
from keel.infra.model_client import ModelClient
from keel.logging import get_logger

_log = get_logger(__name__)

# Path is fixed — it is an artifact checked in alongside the model.
_ROUTER_CONFIG_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "ml"
    / "intent"
    / "artifacts"
    / "router_config.json"
)

_STUB_RESPONSE = (
    "This feature is not yet available. "
    "Please contact your academic advisor or check the student portal."
)

_CHITCHAT_SYSTEM = (
    "You are a helpful university assistant. "
    "Answer the student's question concisely. "
    "If it is not related to university topics, politely redirect."
)
_CHITCHAT_FALLBACK = "I'm here to help with your courses and registration. What can I do for you?"

_STUB_LABELS = {
    "my_info",
}

# Labels that go directly to the agent (not stubs, not direct-LLM).
# Phase 4: grad_apply, major_change, escalate, petition now have agent tools (F1-F4).
_AGENT_LABELS = {
    "plan",
    "advise",
    "audit",
    "whatif",
    "predict",
    "plans_manage",
    "register",
    "waitlist",
    "petition",
    "grad_apply",
    "major_change",
    "escalate",
}

# Labels that use the lite LLM directly.
_LITE_LABELS = {"chitchat", "out_of_scope"}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_fallback_threshold(config_path: Path = _ROUTER_CONFIG_PATH) -> float:
    """Read FALLBACK_THRESHOLD from router_config.json at startup."""
    try:
        with config_path.open() as f:
            data = json.load(f)
        return float(data["fallback_threshold"])
    except Exception as exc:
        _log.warning(
            "router.config_load_failed",
            path=str(config_path),
            error=str(exc),
            fallback=0.5115,
        )
        return 0.5115  # safe default from spec


# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------


@dataclass
class RouterResponse:
    """Full result returned to the API layer."""

    label: str
    confidence: float
    routed_to_agent: bool
    text: str
    router_result: RouterResult = field(init=False)

    def __post_init__(self) -> None:
        self.router_result = RouterResult(
            label=self.label,
            confidence=self.confidence,
            route_to_agent=self.routed_to_agent,
        )


# ---------------------------------------------------------------------------
# Lite-LLM direct handler (chitchat / out_of_scope)
# ---------------------------------------------------------------------------


async def _handle_lite(
    message: str,
    llm_lite: Any,
) -> str:
    """Call the lite LLM with a 50-token cap.  Returns canned response on failure."""
    try:
        llm = llm_lite.bind(max_output_tokens=50)
        result = await llm.ainvoke(
            [SystemMessage(content=_CHITCHAT_SYSTEM), HumanMessage(content=message)]
        )
        content = result.content
        if isinstance(content, list):
            text = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
        else:
            text = str(content).strip()
        return text or _CHITCHAT_FALLBACK
    except Exception as exc:
        _log.warning("router.lite_llm_failed", error=str(exc))
        return _CHITCHAT_FALLBACK


# ---------------------------------------------------------------------------
# Public router entry point
# ---------------------------------------------------------------------------

# Type alias for the agent callable injected at T5.
AgentCallable = Callable[[ContextEnvelope], Coroutine[Any, Any, str]]


async def route(
    *,
    envelope: ContextEnvelope,
    model_client: ModelClient,
    llm_lite: Runnable[Any, Any],
    agent_run: AgentCallable | None = None,
    fallback_threshold: float | None = None,
) -> RouterResponse:
    """Classify the message and dispatch to the appropriate handler.

    Args:
        envelope:           Typed context (message + tenant/student/session).
        model_client:       Async HTTP client to the model-server.
        llm_lite:           Pre-built lite LLM instance (gemini-2.0-flash-lite).
        agent_run:          Async callable that runs the bounded LangGraph agent.
                            If None (T4 phase), agent-bound labels return a stub.
        fallback_threshold: Override the threshold read from router_config.json
                            (primarily for testing).

    Returns:
        RouterResponse with label, confidence, agent-routed flag, and text.
    """
    threshold = fallback_threshold if fallback_threshold is not None else _load_fallback_threshold()

    # --- Intent classification ---
    prediction = await model_client.predict_intent(envelope.message)

    if prediction is None:
        # Model-server unreachable or error → fail-safe to agent.
        _log.warning(
            "router.intent_failed_safe_to_agent",
            tenant_id=envelope.tenant_id,
            session_id=envelope.session_id,
        )
        text = await _run_agent_or_stub(envelope, agent_run)
        return RouterResponse(
            label="unknown",
            confidence=0.0,
            routed_to_agent=True,
            text=text,
        )

    label, confidence = prediction.label, prediction.confidence

    _log.info(
        "router.classified",
        label=label,
        confidence=confidence,
        threshold=threshold,
        tenant_id=envelope.tenant_id,
    )

    # --- Routing decision ---
    if confidence < threshold:
        # Low confidence → agent decides.
        _log.info("router.low_confidence_to_agent", label=label, confidence=confidence)
        text = await _run_agent_or_stub(envelope, agent_run)
        return RouterResponse(label=label, confidence=confidence, routed_to_agent=True, text=text)

    # High-confidence dispatch.
    if label in _LITE_LABELS:
        text = await _handle_lite(envelope.message, llm_lite)
        return RouterResponse(label=label, confidence=confidence, routed_to_agent=False, text=text)

    if label in _AGENT_LABELS:
        text = await _run_agent_or_stub(envelope, agent_run)
        return RouterResponse(label=label, confidence=confidence, routed_to_agent=True, text=text)

    if label in _STUB_LABELS:
        _log.info("router.stub_response", label=label)
        return RouterResponse(
            label=label,
            confidence=confidence,
            routed_to_agent=False,
            text=_STUB_RESPONSE,
        )

    # Unknown label (model drift / new labels not yet in config) → agent.
    _log.warning("router.unknown_label", label=label, confidence=confidence)
    text = await _run_agent_or_stub(envelope, agent_run)
    return RouterResponse(label=label, confidence=confidence, routed_to_agent=True, text=text)


async def _run_agent_or_stub(
    envelope: ContextEnvelope,
    agent_run: AgentCallable | None,
) -> str:
    if agent_run is not None:
        return await agent_run(envelope)
    # T4 stub — agent not wired yet.
    return (
        "I'm processing your request. "
        "(Agent is being set up — this will return a real answer in the next phase.)"
    )
