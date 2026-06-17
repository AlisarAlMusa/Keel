"""Bounded LangGraph agent — single agent, 6-iteration cap, tool allowlist.

Phase 3 additions (spec §2 / plan.md §1.2):
- Resumable graph via Postgres checkpointer (already wired in Phase 2).
- stage_node: tools signal a write request → graph inserts pending action.
- interrupt_node: graph suspends via interrupt(), checkpoints state.
- execute_node: on approved resume → re-validate → single TX write + outbox + audit.
- thread_id = tenant:student:request (stable, scoped, resume-safe).

Architecture:
- One LangGraph agent reached only for hard turns.
- Distinct LLM node and ToolNode (tools are not decorative).
- Max 6 iterations enforced via iteration_count in state.
- Tool allowlist gated in should_continue.
- Answer-or-tool: academic/planning questions MUST use a grounding tool.
- AsyncPostgresSaver for durable checkpointing (survives restarts, long approval pauses).
- Redis session memory for last-N chat history (30-min sliding TTL).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import redis.asyncio as aioredis
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from keel.agent.state import AgentState
from keel.agent.tools import AgentDeps, make_tools
from keel.domain.schemas import ContextEnvelope
from keel.infra.guardrails import redact
from keel.logging import get_logger

_log = get_logger(__name__)

_MAX_ITERATIONS = 6
_SYSTEM_PROMPT_VERSION = "v2"

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt_template(version: str) -> str:
    """Load system prompt from agent/prompts/system_{version}.md at import time."""
    path = _PROMPTS_DIR / f"system_{version}.md"
    text = path.read_text()
    # Strip comment lines (lines starting with #) so they don't reach the LLM.
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return "\n".join(lines).strip()


_SYSTEM_PROMPT_TEMPLATE = _load_prompt_template(_SYSTEM_PROMPT_VERSION)
_SESSION_KEY_PREFIX = "session"
_SNAPSHOT_KEY_PREFIX = "snapshot"
_SNAPSHOT_TTL = 300  # 5 min; invalidation hooks land in Phase 3
_SESSION_N = 10  # last N messages from Redis session history

# Action types that trigger the stage → interrupt → execute pattern.
_STAGE_TOOL_NAMES = {"stage_enrollment", "stage_waitlist_join", "stage_waitlist_leave"}


def _extract_text(content: Any) -> str:
    """Extract plain text from an AIMessage.content that may be a list of blocks (Gemini)."""
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
            if not isinstance(block, dict) or block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _session_key(tenant_id: str, session_id: str) -> str:
    return f"{_SESSION_KEY_PREFIX}:{tenant_id}:{session_id}"


def _snapshot_key(tenant_id: str, student_id: str) -> str:
    return f"{_SNAPSHOT_KEY_PREFIX}:{tenant_id}:{student_id}"


def _system_prompt(context: ContextEnvelope, snapshot: dict[str, Any] | None) -> str:
    snap_text = ""
    if snapshot:
        snap_text = "\n\nStudent snapshot (engine-computed, authoritative):\n" + json.dumps(
            snapshot, indent=2
        )
    return _SYSTEM_PROMPT_TEMPLATE.format(
        student_id=context.student_id,
        tenant_id=context.tenant_id,
        snapshot=snap_text,
    )


async def _load_session_history(
    redis: aioredis.Redis,
    tenant_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    key = _session_key(tenant_id, session_id)
    try:
        raw = await redis.get(key)
        if raw:
            return cast(list[dict[str, Any]], json.loads(raw))[-_SESSION_N * 2 :]
    except Exception as exc:
        _log.warning("agent.session_load_failed", error=str(exc))
    return []


async def _save_session_history(
    redis: aioredis.Redis,
    tenant_id: str,
    session_id: str,
    messages: list[dict[str, Any]],
    ttl: int,
) -> None:
    key = _session_key(tenant_id, session_id)
    try:
        await redis.set(key, json.dumps(messages), ex=ttl)
    except Exception as exc:
        _log.warning("agent.session_save_failed", error=str(exc))


async def _load_snapshot(
    redis: aioredis.Redis,
    tenant_id: str,
    student_id: str,
) -> dict[str, Any] | None:
    key = _snapshot_key(tenant_id, student_id)
    try:
        raw = await redis.get(key)
        if raw:
            return json.loads(raw)  # type: ignore[no-any-return]
    except Exception as exc:
        _log.warning("agent.snapshot_load_failed", error=str(exc))
    return None


async def _save_snapshot(
    redis: aioredis.Redis,
    tenant_id: str,
    student_id: str,
    snapshot: dict[str, Any],
) -> None:
    key = _snapshot_key(tenant_id, student_id)
    try:
        await redis.set(key, json.dumps(snapshot), ex=_SNAPSHOT_TTL)
    except Exception as exc:
        _log.warning("agent.snapshot_save_failed", error=str(exc))


def build_agent(
    llm: Any,
    deps: AgentDeps,
    checkpointer: AsyncPostgresSaver,
) -> Any:
    """Build and compile the bounded LangGraph agent.

    Returns a CompiledStateGraph. Call once in lifespan; reuse per request.
    The checkpointer (AsyncPostgresSaver) makes the graph durable — it can
    resume across server restarts and long approval pauses (spec §1).
    """
    tools = make_tools(deps)
    tool_names = {t.name for t in tools}
    llm_with_tools = llm.bind_tools(tools)

    # --- LLM node ---

    async def llm_node(state: AgentState) -> dict[str, Any]:
        count = state.get("iteration_count", 0) + 1

        if count > _MAX_ITERATIONS:
            _log.warning("agent.max_iterations_reached", count=count)
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "I've reached the maximum number of steps for this request. "
                            "Please ask your academic advisor for further help."
                        )
                    )
                ],
                "iteration_count": count,
            }

        context: ContextEnvelope = state["context"]
        snapshot = state.get("student_snapshot")
        system = _system_prompt(context, snapshot)

        history = [SystemMessage(content=system)] + list(state.get("messages", []))
        response = await llm_with_tools.ainvoke(history)

        _log.info(
            "agent.llm_node",
            iteration=count,
            tool_calls=len(getattr(response, "tool_calls", []) or []),
            content_type=type(response.content).__name__,
            content_preview=str(response.content)[:120] if response.content else "<empty>",
            tenant_id=context.tenant_id,
        )
        return {"messages": [response], "iteration_count": count}

    # --- Tool node (read-only tools only — write tools return action_id) ---

    def should_continue(state: AgentState) -> str:
        messages = state.get("messages", [])
        if not messages:
            return END
        last = messages[-1]
        count = state.get("iteration_count", 0)
        if count >= _MAX_ITERATIONS:
            return END
        if hasattr(last, "tool_calls") and last.tool_calls:
            for call in last.tool_calls:
                if call["name"] not in tool_names:
                    _log.warning("agent.tool_not_allowed", name=call["name"])
                    return END
                if call["name"] in _STAGE_TOOL_NAMES:
                    return "stage"
            return "tools"
        return END

    # --- Stage node — runs after a stage_* tool call ---

    async def stage_node(state: AgentState) -> dict[str, Any]:
        """Execute the stage tool and interrupt the graph for human approval.

        After the ToolNode runs stage_enrollment (or stage_waitlist_*), the last
        ToolMessage contains the JSON result with action_id + summary.  We interrupt
        here so control returns to the human with the pending action visible.
        """
        messages = state.get("messages", [])
        # Find the most recent ToolMessage (result of the stage_* tool call).
        action_summary = "A pending action is awaiting your approval."
        action_id: str | None = None
        for msg in reversed(messages):
            if hasattr(msg, "content") and isinstance(msg.content, str):
                try:
                    data = json.loads(msg.content)
                    if "action_id" in data:
                        action_id = data["action_id"]
                        action_summary = data.get("message", action_summary)
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

        _log.info(
            "agent.stage_node.interrupt",
            action_id=action_id,
            tenant_id=state["context"].tenant_id if "context" in state else "unknown",
        )

        # interrupt() suspends execution and checkpoints state.
        # Control returns to the caller (run_agent / approve handler).
        human_input = interrupt({"action_id": action_id, "summary": action_summary})

        # On resume: human_input contains {"action_id": ..., "rejected": bool}.
        return {"pending_action_id": action_id, "resume_payload": human_input}

    # --- Execute node — runs on approved resume ---

    async def execute_node(state: AgentState) -> dict[str, Any]:
        """Execute the approved write action from the frozen payload (spec §1).

        Reads the action row — never LLM-emitted args after resume.
        Re-validates engine constraints before writing.
        Single transaction: domain write + outbox + audit → action.executed.
        """
        resume_payload = state.get("resume_payload", {})
        action_id_str = (
            resume_payload.get("action_id") if isinstance(resume_payload, dict) else None
        )
        if not action_id_str:
            return {"messages": [AIMessage(content="No action to execute.")]}

        if isinstance(resume_payload, dict) and resume_payload.get("rejected"):
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Understood — I won't proceed with that action. "
                            "Let me know if you'd like to explore other options."
                        )
                    )
                ]
            }

        context: ContextEnvelope = state["context"]
        tenant_id = UUID(context.tenant_id)
        student_id = UUID(context.student_id)

        try:
            from keel.infra.database.session import tenant_session as _ts

            # Load the frozen action payload.
            async with _ts(deps.session_factory, tenant_id) as session:
                from keel.services.actions import ActionRepo as _AR

                action = await _AR.get(session, UUID(action_id_str))

                if not action:
                    return {"messages": [AIMessage(content=f"Action {action_id_str} not found.")]}

                if str(action["status"]) != "approved":
                    _log.warning(
                        "agent.execute_node.not_approved",
                        action_id=action_id_str,
                        status=action["status"],
                    )
                    return {
                        "messages": [
                            AIMessage(
                                content=(
                                    "I can only execute an approved action. "
                                    f"This action is '{action['status']}'."
                                )
                            )
                        ]
                    }

                action_type = str(action["type"])
                payload = dict(action["payload"]) if action["payload"] else {}

            # Dispatch to the correct service function — reads FROZEN payload only.
            result_msg = await _dispatch_execute(
                action_type=action_type,
                action_id=UUID(action_id_str),
                payload=payload,
                tenant_id=tenant_id,
                student_id=student_id,
                deps=deps,
            )

            _log.info(
                "agent.execute_node.done",
                action_id=action_id_str,
                action_type=action_type,
                tenant_id=str(tenant_id),
            )
            return {"messages": [AIMessage(content=result_msg)]}

        except Exception as exc:
            _log.error("agent.execute_node.error", error=str(exc), action_id=action_id_str)
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "There was an error processing your action. "
                            "Please try again or contact your advisor."
                        )
                    )
                ]
            }

    # --- Graph wiring ---

    graph = StateGraph(AgentState)
    graph.add_node("llm", llm_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("stage", ToolNode(tools))  # runs stage_* tool then suspends
    graph.add_node("interrupt", stage_node)  # suspends graph; human approves
    graph.add_node("execute", execute_node)  # post-approval write

    graph.set_entry_point("llm")
    graph.add_conditional_edges(
        "llm",
        should_continue,
        {"tools": "tools", "stage": "stage", END: END},
    )
    graph.add_edge("tools", "llm")
    graph.add_edge("stage", "interrupt")  # after stage_* tool runs, interrupt
    graph.add_edge("interrupt", "execute")  # after human approves, execute
    graph.add_edge("execute", "llm")  # agent continues after write

    return graph.compile(checkpointer=checkpointer, interrupt_before=["interrupt"])


async def _dispatch_execute(
    *,
    action_type: str,
    action_id: UUID,
    payload: dict[str, Any],
    tenant_id: UUID,
    student_id: UUID,
    deps: AgentDeps,
) -> str:
    """Route approved action to its deterministic service function."""
    from keel.infra.database.session import tenant_session as _ts

    tx_result: Any

    if action_type == "enrollment":
        from keel.services.actions.enrollment import execute_enrollment_tx

        section_ids: list[str] = payload.get("section_ids", [])

        async with _ts(deps.session_factory, tenant_id) as session:
            tx_result = await execute_enrollment_tx(
                session,
                action_id=action_id,
                tenant_id=tenant_id,
                student_id=student_id,
                section_ids=section_ids,
            )
        return str(tx_result.message)

    elif action_type == "waitlist_join":
        from keel.services.actions.waitlist_service import join_waitlist_tx

        section_id = UUID(str(payload["section_id"]))
        auto_enroll = bool(payload.get("auto_enroll", False))
        async with _ts(deps.session_factory, tenant_id) as session:
            tx_result = await join_waitlist_tx(
                session,
                action_id=action_id,
                tenant_id=tenant_id,
                student_id=student_id,
                section_id=section_id,
                auto_enroll=auto_enroll,
            )
        return str(tx_result.message)

    elif action_type == "waitlist_leave":
        from keel.services.actions.waitlist_service import leave_waitlist_tx

        section_id = UUID(str(payload["section_id"]))
        async with _ts(deps.session_factory, tenant_id) as session:
            tx_result = await leave_waitlist_tx(
                session,
                action_id=action_id,
                tenant_id=tenant_id,
                student_id=student_id,
                section_id=section_id,
            )
        return str(tx_result.message)

    else:
        return f"Unknown action type: {action_type}"


async def run_agent(
    *,
    envelope: ContextEnvelope,
    compiled_graph: Any,
    redis: aioredis.Redis,
    session_ttl: int,
) -> str:
    """Run one turn of the agent and return the final text response.

    Loads session history from Redis, runs the graph, saves updated history.
    Redacts the response before returning.
    """
    history_dicts = await _load_session_history(redis, envelope.tenant_id, envelope.session_id)
    snapshot = await _load_snapshot(redis, envelope.tenant_id, envelope.student_id)

    prior: list[Any] = []
    for d in history_dicts:
        role = d.get("role")
        content = d.get("content", "")
        if role == "human":
            prior.append(HumanMessage(content=content))
        elif role == "ai":
            prior.append(AIMessage(content=content))

    prior.append(HumanMessage(content=envelope.message))

    initial_state: AgentState = {
        "messages": prior,
        "context": envelope,
        "iteration_count": 0,
        "student_snapshot": snapshot,
    }

    config = {
        "configurable": {
            "thread_id": f"{envelope.tenant_id}:{envelope.session_id}",
        }
    }

    try:
        final_state = await compiled_graph.ainvoke(initial_state, config=config)
    except Exception as exc:
        _log.error("agent.run_failed", error=str(exc), session_id=envelope.session_id)
        return "I encountered an error processing your request. Please try again."

    messages = final_state.get("messages", [])
    response_text = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            response_text = _extract_text(msg.content)
            break
    if not response_text:
        response_text = "I was unable to generate a response. Please try again."

    response_text = redact(response_text)

    history_dicts.append({"role": "human", "content": envelope.message})
    history_dicts.append({"role": "ai", "content": response_text})
    await _save_session_history(
        redis, envelope.tenant_id, envelope.session_id, history_dicts, session_ttl
    )

    _log.info(
        "agent.turn_complete",
        session_id=envelope.session_id,
        tenant_id=envelope.tenant_id,
        iterations=final_state.get("iteration_count", 0),
        response_len=len(response_text),
    )
    return response_text
