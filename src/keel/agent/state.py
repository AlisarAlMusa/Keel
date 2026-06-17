"""LangGraph agent state for the Keel bounded agent.

AgentState is the single dict that flows through every node.
All fields are optional at init and accumulated as the graph runs.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from keel.domain.schemas import ContextEnvelope


class AgentState(TypedDict, total=False):
    """Mutable state threaded through every graph node.

    messages:          Full LangGraph message list (add_messages reducer handles
                       append-only semantics — do not overwrite directly).
    context:           Immutable ContextEnvelope for this turn.
    iteration_count:   Incremented each time llm_node runs; hard-stops at 6.
    student_snapshot:  Engine-computed snapshot dict (audit summary + active plan
                       ref), cached per student in Redis; None until first load.
    pending_action_id: UUID str of the action staged and awaiting human approval.
                       Set by stage_node; consumed by execute_node on resume.
    resume_payload:    Value returned by interrupt() when the graph is resumed.
                       Contains {"action_id": str, "rejected": bool} from the
                       approve/reject handler. execute_node reads this; ignores
                       any LLM-emitted args after resume (frozen payload).
    """

    messages: Annotated[list[BaseMessage], add_messages]
    context: ContextEnvelope
    iteration_count: int
    student_snapshot: dict[str, Any] | None
    pending_action_id: str | None
    resume_payload: dict[str, Any] | None
