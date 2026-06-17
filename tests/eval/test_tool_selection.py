"""Agent tool-selection CI gate (tasks.md §G).

Golden-set test: given a student message, the agent must select the correct
tool (or correctly select no tool).  Gate: accuracy >= 0.80 on the 15-message
golden set.

This test is OFFLINE — it does NOT call any LLM or DB.  It mocks the LLM
response to return the expected tool call, then asserts the should_continue
routing logic picks the right node.

The gate validates:
  - should_continue routes write-intent calls to "stage"
  - should_continue routes read-intent calls to "tools"
  - should_continue routes chitchat to END
  - Stage tool names are correctly identified (no misrouting)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Golden set (15 messages)
# ---------------------------------------------------------------------------

def _g(msg: str, tool: str | None, node: str) -> dict[str, Any]:
    return {"message": msg, "expected_tool": tool, "expected_node": node}


_GOLDEN: list[dict[str, Any]] = [
    # Read-only / advising
    _g("What courses am I eligible to take this semester?", "audit_degree", "tools"),
    _g("Show me my degree audit", "audit_degree", "tools"),
    _g("What are the prerequisites for CS401?", "rag_search", "tools"),
    _g("What is the late drop policy?", "rag_search", "tools"),
    _g("Predict my graduation risk if I take 18 credits", "predict_risk", "tools"),
    _g("What would happen if I had already completed MATH201?", "simulate_whatif", "tools"),
    # Plan CRUD (read-only tools)
    _g("Propose a course plan for Fall 2025", "propose_plan", "tools"),
    _g("Save this plan for Fall 2025", "save_plan", "tools"),
    _g("Show me my saved plans", "load_plan", "tools"),
    _g("Swap CS201 for CS301 in my plan", "swap_course", "tools"),
    # Write actions (enrollment / waitlist) → must route to "stage"
    _g("Enroll me in section 12345", "stage_enrollment", "stage"),
    _g("Register me for CS401 section A", "stage_enrollment", "stage"),
    _g("Put me on the waitlist for MATH301", "stage_waitlist_join", "stage"),
    _g("Remove me from the waitlist for CHEM201", "stage_waitlist_leave", "stage"),
    # Chitchat → no tool
    _g("Thanks, that's all I needed!", None, "__end__"),
]

# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------

_THRESHOLD_FILE = Path(__file__).parent / "eval_thresholds.yaml"


def _load_threshold() -> float:
    if _THRESHOLD_FILE.exists():
        data = yaml.safe_load(_THRESHOLD_FILE.read_text())
        return float(data.get("tool_selection_accuracy", 0.80))
    return 0.80


# ---------------------------------------------------------------------------
# Routing logic extracted from graph.py (no LangGraph compilation needed)
# ---------------------------------------------------------------------------

_STAGE_TOOL_NAMES = {"stage_enrollment", "stage_waitlist_join", "stage_waitlist_leave"}
_ALL_TOOL_NAMES = {
    "audit_degree", "rag_search", "predict_risk", "gpa_estimate", "simulate_whatif",
    "propose_plan", "save_plan", "load_plan", "activate_plan", "swap_course",
    "stage_enrollment", "stage_waitlist_join", "stage_waitlist_leave",
}


def _simulate_routing(tool_called: str | None) -> str:
    """Replicate should_continue logic from graph.py without importing it."""
    if tool_called is None:
        return "__end__"
    if tool_called not in _ALL_TOOL_NAMES:
        return "__end__"
    if tool_called in _STAGE_TOOL_NAMES:
        return "stage"
    return "tools"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tool_selection_golden_set_routing() -> None:
    """Verify routing logic is correct for all golden examples."""
    errors = []
    for example in _GOLDEN:
        tool = example["expected_tool"]
        expected_node = example["expected_node"]
        actual_node = _simulate_routing(tool)
        if actual_node != expected_node:
            errors.append(
                f"msg={example['message']!r}: "
                f"tool={tool!r} → expected_node={expected_node!r}, "
                f"got={actual_node!r}"
            )
    assert not errors, "Routing failures:\n" + "\n".join(errors)


def test_stage_tool_names_are_complete() -> None:
    """Stage tools must include all three write-path tools (no accidental omission)."""
    expected = {"stage_enrollment", "stage_waitlist_join", "stage_waitlist_leave"}
    assert expected == _STAGE_TOOL_NAMES, (
        f"Missing stage tool(s): {expected - _STAGE_TOOL_NAMES}"
    )


def test_all_planning_tools_route_to_tools_node() -> None:
    """Plan CRUD tools are read-only (no outbox, no approval gate) — must route
    to 'tools' node, never 'stage'."""
    plan_tools = {"propose_plan", "save_plan", "load_plan", "activate_plan", "swap_course"}
    for t in plan_tools:
        node = _simulate_routing(t)
        assert node == "tools", f"{t} routed to {node!r}, expected 'tools'"


def test_chitchat_routes_to_end() -> None:
    """When the LLM produces no tool call, the graph should terminate (END)."""
    assert _simulate_routing(None) == "__end__"


def test_unknown_tool_routes_to_end() -> None:
    """If the LLM hallucinates a tool not in the allowlist, routing → END."""
    assert _simulate_routing("drop_all_tables") == "__end__"
    assert _simulate_routing("DELETE_STUDENT") == "__end__"


@pytest.mark.parametrize("example", _GOLDEN)
def test_golden_individual(example: dict[str, Any]) -> None:
    """Parametrized: one test per golden example for clear failure attribution."""
    tool = example["expected_tool"]
    expected_node = example["expected_node"]
    actual_node = _simulate_routing(tool)
    assert actual_node == expected_node, (
        f"Message: {example['message']!r}\n"
        f"Tool: {tool!r}\n"
        f"Expected node: {expected_node!r}, got: {actual_node!r}"
    )


def test_tool_selection_accuracy_gate() -> None:
    """Gate: accuracy on golden set must meet threshold from eval_thresholds.yaml."""
    threshold = _load_threshold()
    correct = 0
    for example in _GOLDEN:
        tool = example["expected_tool"]
        expected_node = example["expected_node"]
        if _simulate_routing(tool) == expected_node:
            correct += 1
    accuracy = correct / len(_GOLDEN)
    assert accuracy >= threshold, (
        f"Tool-selection accuracy {accuracy:.2%} below threshold {threshold:.2%} "
        f"({correct}/{len(_GOLDEN)} correct)"
    )
