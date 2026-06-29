"""Agent tools package.

Exports AgentDeps and make_tools() — the only public surface.
Callers (graph.py, main.py) import from `keel.agent.tools` unchanged.

Tool groups:
  advising.py   — audit_degree, rag_search  (+ predict_risk, gpa_estimate in Phase 3)
  planning.py   — propose_plan, plan_graduation, active grad-plan metadata tools
  enrollment.py — stage_enrollment, stage_waitlist_join, stage_waitlist_leave  (Phase 3)
"""

from __future__ import annotations

from typing import Any

from ._deps import AgentDeps
from .advising import make_advising_chat_tools, make_advising_tools
from .enrollment import make_enrollment_tools
from .guidance import make_guidance_tools
from .institutional import make_institutional_tools
from .planning import make_planning_tools

__all__ = ["AgentDeps", "make_tools"]


def make_tools(deps: AgentDeps) -> list[Any]:
    """Assemble all agent tools closed over their dependencies.

    Phase 4 adds the advising-chat (C1–C4), guidance (E1/E2), and
    institutional (F1–F4) tool groups. The four F-tools are PROPOSAL-ONLY and
    expose no ``approved`` flag — the write gate lives outside the agent.
    """
    return [
        *make_advising_tools(deps),
        *make_advising_chat_tools(deps),  # C1–C4 (read-only)
        *make_planning_tools(deps),
        *make_guidance_tools(deps),  # E1, E2, E2-save
        *make_enrollment_tools(deps),
        *make_institutional_tools(deps),  # F1–F4 (proposal-only writes)
    ]
