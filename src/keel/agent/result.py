"""Structured result of one agent turn.

``run_agent`` previously returned only a string, so the staged-action ``action_id``
and the "awaiting approval" signal produced inside the graph never reached the
chat endpoint — and therefore never reached the widget, which needs ``action_id``
+ ``pending_approval`` to render the Approve/Decline control. This type threads
that signal out so the chat → widget contract is complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    text: str
    action_id: str | None = None
    pending_approval: bool = False
    # G3: structured plan cards emitted by propose_plan this turn (widget PlanData).
    plans: list[dict[str, Any]] = field(default_factory=list)
