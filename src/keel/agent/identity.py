"""Verified per-request identity for agent tools (spec §11).

The bounded agent's tools historically received ``tenant_id`` / ``student_id`` as
LLM-generated arguments ("copy from the system prompt"). That makes identity
LLM-controllable: a prompt injection could pass a *different* tenant_id, and since
every tool opens ``tenant_session(UUID(tenant_id))`` from that argument, a read
tool would then be RLS-scoped to the WRONG tenant — a cross-tenant data leak.

Fix: the chat request handler / ``run_agent`` binds the verified identity (from
the widget JWT, never from the LLM) into a ``ContextVar`` for the duration of the
turn. Tools call :func:`resolve_identity` to get the EFFECTIVE identity, which is
the verified value whenever it is set — the LLM-supplied arguments are ignored
(and a mismatch is logged as a tampering signal). The arguments stay in the tool
schemas only so the LLM keeps producing well-formed calls; they no longer decide
which tenant's data is touched.

ContextVars propagate across ``await`` within the same asyncio task, so a value
set before ``compiled_graph.ainvoke(...)`` is visible inside every awaited tool.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

from keel.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class RequestIdentity:
    tenant_id: str
    student_id: str
    thread_id: str | None = None


_identity: contextvars.ContextVar[RequestIdentity | None] = contextvars.ContextVar(
    "keel_request_identity", default=None
)


def set_request_identity(
    tenant_id: str, student_id: str, thread_id: str | None = None
) -> contextvars.Token[RequestIdentity | None]:
    """Bind the verified identity + the runtime LangGraph thread_id for the turn.

    ``thread_id`` is the graph's real checkpoint key (``tenant:session``). Stage
    tools must use this — never an LLM-supplied value — or the approval resume
    targets the wrong (or a non-existent) thread and the write never executes.
    Returns a reset token.
    """
    return _identity.set(
        RequestIdentity(
            tenant_id=str(tenant_id), student_id=str(student_id), thread_id=thread_id
        )
    )


def reset_request_identity(token: contextvars.Token[RequestIdentity | None]) -> None:
    _identity.reset(token)


def resolve_identity(arg_tenant_id: str, arg_student_id: str) -> tuple[str, str]:
    """Return the effective (tenant_id, student_id) for a tool call.

    When a verified identity is bound (normal request path), it WINS — the
    LLM-supplied arguments are ignored. A mismatch is logged because it means the
    model tried to act for a different tenant/student than the authenticated one.
    When no identity is bound (e.g. unit tests that call tools directly), the
    arguments are used as-is.
    """
    verified = _identity.get()
    if verified is None:
        return str(arg_tenant_id), str(arg_student_id)

    if str(arg_tenant_id) != verified.tenant_id or str(arg_student_id) != verified.student_id:
        _log.warning(
            "agent.identity_override",
            verified_tenant=verified.tenant_id,
            arg_tenant=str(arg_tenant_id),
            student_mismatch=str(arg_student_id) != verified.student_id,
        )
    return verified.tenant_id, verified.student_id


def resolve_thread_id(arg_thread_id: str) -> str:
    """Return the runtime LangGraph thread_id, ignoring the LLM-supplied value.

    The graph's thread_id (``tenant:session``) is the checkpoint key the approval
    resume uses. LLMs reliably get this wrong (e.g. echoing the student_id), which
    silently breaks execution — so the bound runtime value always wins.
    """
    verified = _identity.get()
    if verified is None or not verified.thread_id:
        return str(arg_thread_id)
    return verified.thread_id


def resolve_tenant(arg_tenant_id: str) -> str:
    """Tenant-only variant of :func:`resolve_identity` for tools that take a
    ``tenant_id`` but no ``student_id`` (e.g. rag_search)."""
    verified = _identity.get()
    if verified is None:
        return str(arg_tenant_id)
    if str(arg_tenant_id) != verified.tenant_id:
        _log.warning(
            "agent.identity_override",
            verified_tenant=verified.tenant_id,
            arg_tenant=str(arg_tenant_id),
        )
    return verified.tenant_id
