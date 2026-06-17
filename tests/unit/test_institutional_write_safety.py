"""Day-5 write-action-safety gate (unit half) — institutional writes (spec §5, App. B).

These assertions need no DB and no LLM; they prove the approval gate cannot be
bypassed *by construction*:

  1. No agent F-tool exposes an ``approved`` parameter — the LLM literally cannot
     request a write, so prompt injection ("file it now without approval") can't
     flip a gate that isn't reachable from tool args (Appendix B: injection-never-writes).
  2. Every institutional service function defaults ``approved=False``.
  3. Calling a service function with ``approved=False`` performs ZERO database work
     (the session factory is never even opened) → no queue row, no outbox row.

The DB-dependent properties (idempotency, cross-tenant isolation, petition-never-
enrolls) live in ``tests/integration/test_institutional_write_safety.py``.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from keel.services.actions import institutional as inst

_F_SERVICE = [
    inst.apply_graduation,
    inst.request_major_change,
    inst.submit_petition,
    inst.escalate,
]

_AGENT_F_TOOLS = {"apply_graduation", "request_major_change", "submit_petition", "escalate"}


# ---------------------------------------------------------------------------
# 1. Agent F-tools expose no `approved` parameter (injection-safe by construction)
# ---------------------------------------------------------------------------


def test_agent_f_tools_have_no_approved_param() -> None:
    from keel.agent.tools import make_tools

    deps = MagicMock()
    from keel.domain.models import Term

    deps.current_term = Term.FALL
    deps.current_year = 2026

    tools = {t.name: t for t in make_tools(deps) if t.name in _AGENT_F_TOOLS}
    assert set(tools) == _AGENT_F_TOOLS, f"missing F-tools: {_AGENT_F_TOOLS - set(tools)}"

    for name, tool in tools.items():
        fields = set(tool.args_schema.model_fields.keys())
        assert "approved" not in fields, (
            f"{name} exposes an 'approved' arg — the LLM could self-approve a write"
        )


# ---------------------------------------------------------------------------
# 2. Service functions default approved=False
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fn", _F_SERVICE, ids=lambda f: f.__name__)
def test_service_fn_defaults_to_not_approved(fn) -> None:  # type: ignore[no-untyped-def]
    sig = inspect.signature(fn)
    assert "approved" in sig.parameters, f"{fn.__name__} has no approval gate"
    assert sig.parameters["approved"].default is False, (
        f"{fn.__name__} must default approved=False"
    )


# ---------------------------------------------------------------------------
# 3. approved=False → zero DB work (no write of any kind)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_graduation_no_write_without_approval() -> None:
    factory = MagicMock(side_effect=AssertionError("DB opened without approval"))
    result = await inst.apply_graduation(
        factory, tenant_id=uuid4(), student_id=uuid4(), program="BSCS", approved=False
    )
    assert result.written is False
    assert result.request_id is None
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_request_major_change_no_write_without_approval() -> None:
    factory = MagicMock(side_effect=AssertionError("DB opened without approval"))
    result = await inst.request_major_change(
        factory, tenant_id=uuid4(), student_id=uuid4(), target_program_id="BSDS", approved=False
    )
    assert result.written is False
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_submit_petition_no_write_without_approval() -> None:
    factory = MagicMock(side_effect=AssertionError("DB opened without approval"))
    result = await inst.submit_petition(
        factory,
        tenant_id=uuid4(),
        student_id=uuid4(),
        course_id="CS301",
        justification="x",
        approved=False,
    )
    assert result.written is False
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_escalate_no_send_without_approval() -> None:
    factory = MagicMock(side_effect=AssertionError("DB opened without approval"))
    result = await inst.escalate(
        factory, tenant_id=uuid4(), student_id=uuid4(), reason="help", approved=False
    )
    assert result.sent is False
    factory.assert_not_called()
