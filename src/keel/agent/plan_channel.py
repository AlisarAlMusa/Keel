"""Side channel for surfacing structured plan cards out of the agent (G3).

``propose_plan`` builds engine-verified, risk-scored candidate plans, but its tool
return value is consumed by the LLM as text — the structured data never reached the
chat response, so the widget (which already renders rich ``PlanData`` cards) only
ever showed the model's prose. This channel threads the structured plans out.

Why a mutable-list container rather than a plain ContextVar value:
  A ContextVar *value* set inside a tool does not reliably propagate back UP to
  ``run_agent`` — LangGraph may execute the node under a copied context, so a
  reassignment in the child is invisible to the parent. Binding a single mutable
  list in the parent BEFORE invoke and having the tool ``.append`` to it works
  regardless: a context copy shares the same list object, so the parent sees the
  appended items. (Same direction-of-flow caveat is why identity binding — parent
  → child — uses a plain value, but plan collection — child → parent — needs this.)

The list is per-turn: ``run_agent`` binds a fresh one, reads it after the graph
finishes, and resets. Outside a bound turn (e.g. unit tests calling the tool
directly) the collector is ``None`` and the tool simply skips emitting.
"""

from __future__ import annotations

import contextvars
from typing import Any

_plan_channel: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "keel_plan_channel", default=None
)


def bind_plan_channel() -> contextvars.Token[list[dict[str, Any]] | None]:
    """Bind a fresh empty collector for this turn. Returns a reset token."""
    return _plan_channel.set([])


def reset_plan_channel(token: contextvars.Token[list[dict[str, Any]] | None]) -> None:
    _plan_channel.reset(token)


def emit_plans(plans: list[dict[str, Any]]) -> None:
    """Append structured plan cards to the active collector (no-op if unbound)."""
    collector = _plan_channel.get()
    if collector is not None:
        collector.extend(plans)


def collected_plans() -> list[dict[str, Any]]:
    """Return the plans collected this turn (empty if none / unbound)."""
    collector = _plan_channel.get()
    return list(collector) if collector else []
