"""Agent tools package.

Exports AgentDeps and make_tools() — the only public surface.
Callers (graph.py, main.py) import from `keel.agent.tools` unchanged.

Tool groups:
  advising.py   — audit_degree, rag_search  (+ predict_risk, gpa_estimate in Phase 3)
  planning.py   — propose_plan              (+ simulate_whatif, save/load/activate/swap in Phase 3)
  enrollment.py — stage_enrollment, stage_waitlist_join, stage_waitlist_leave  (Phase 3)
"""

from __future__ import annotations

from typing import Any

from ._deps import AgentDeps
from .advising import make_advising_tools
from .enrollment import make_enrollment_tools
from .planning import make_planning_tools

__all__ = ["AgentDeps", "make_tools"]


def make_tools(deps: AgentDeps) -> list[Any]:
    """Assemble all agent tools closed over their dependencies."""
    return [
        *make_advising_tools(deps),
        *make_planning_tools(deps),
        *make_enrollment_tools(deps),
    ]
