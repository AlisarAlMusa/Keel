"""Agent-level span helpers (tracing the bounded LangGraph turn).

The infra auto-instrumentation (FastAPI/SQLAlchemy/Redis/httpx in
``keel.infra.tracing``) already captures the request, DB, cache and the outbound
LLM/model-server HTTP calls. This module adds the *reasoning* spans that those
can't see: the agent turn, each LLM step (with the model's tool-call decisions),
and each tool invocation (with its inputs and a preview of its output).

Kept in ``agent/`` (not ``infra/``) on purpose: it imports LangChain tool types,
which the infra layer must stay free of. All wrapping is best-effort — if a tool
can't be wrapped, the original is used unchanged so tracing never breaks the agent.
"""

from __future__ import annotations

import json
from typing import Any

from opentelemetry import trace

from keel.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer("keel.agent")

# Cap attribute sizes so a span never carries a full transcript/plan blob.
_PREVIEW = 500


def _preview(value: Any) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text[:_PREVIEW]


def get_tracer() -> trace.Tracer:
    """The agent tracer — used by graph.py for the turn and LLM-step spans."""
    return _tracer


def traced_tools(tools: list[Any]) -> list[Any]:
    """Return copies of ``tools`` whose async call is wrapped in a span.

    Each span is named ``agent.tool.<name>`` and records the tool input args and
    a preview of the result, so a chat turn shows exactly which tool ran, with
    what arguments, and what it returned. The verified-identity binding still
    happens inside the tool (resolve_identity) — this only observes.
    """
    wrapped: list[Any] = []
    for tool in tools:
        coro = getattr(tool, "coroutine", None)
        name = getattr(tool, "name", getattr(tool, "__name__", "tool"))
        if coro is None:
            wrapped.append(tool)
            continue
        try:
            wrapped.append(tool.model_copy(update={"coroutine": _wrap_coro(coro, name)}))
        except Exception as exc:  # noqa: BLE001 — never let tracing drop a tool
            _log.warning("tool_span_wrap_failed", tool=name, error=type(exc).__name__)
            wrapped.append(tool)
    return wrapped


def _wrap_coro(coro: Any, name: str) -> Any:
    async def _traced(*args: Any, **kwargs: Any) -> Any:
        with _tracer.start_as_current_span(f"agent.tool.{name}") as span:
            span.set_attribute("keel.tool.name", name)
            if kwargs:
                span.set_attribute("keel.tool.input", _preview(kwargs))
            result = await coro(*args, **kwargs)
            span.set_attribute("keel.tool.output", _preview(result))
            return result

    return _traced
