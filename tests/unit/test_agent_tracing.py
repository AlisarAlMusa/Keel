"""Agent tracing helpers: tool-span wrapping must be transparent.

Wrapping a tool in a span must never change its result or drop the tool, and a
tool that can't be wrapped (no coroutine) must pass through unchanged. These are
the invariants that keep tracing from ever breaking the agent.
"""

from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

from keel.agent.tracing import traced_tools


def _make_async_tool(name: str):
    async def _run(x: int) -> str:
        return f"{name}:{x * 2}"

    return StructuredTool.from_function(coroutine=_run, name=name, description=name)


@pytest.mark.asyncio
async def test_wrapped_tool_returns_same_result() -> None:
    tool = _make_async_tool("double")
    (wrapped,) = traced_tools([tool])
    # Same identity surface (name/args schema) and same result through the span.
    assert wrapped.name == "double"
    assert await wrapped.coroutine(x=21) == "double:42"


def test_wrapping_preserves_count_and_names() -> None:
    tools = [_make_async_tool("a"), _make_async_tool("b")]
    wrapped = traced_tools(tools)
    assert [t.name for t in wrapped] == ["a", "b"]


def test_tool_without_coroutine_passes_through_unchanged() -> None:
    sync_tool = StructuredTool.from_function(
        func=lambda x: x, name="sync_only", description="sync"
    )
    (result,) = traced_tools([sync_tool])
    # No async path to wrap → the original object is returned as-is.
    assert result is sync_tool
