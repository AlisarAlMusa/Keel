"""Unit tests for the section-selection registration flow.

Covers:
1. `_after_stage` (graph.py): a staged action (has action_id) pauses for approval;
   a ToolError (no action_id) routes back to the LLM — the Piece 1 routing fix that
   stops a failed enrollment from silently looping.
2. Time-preference helpers (`_slots_meet_prefs`, `_fmt_slots`).
3. `_resolve_sections_for_courses`: preference-aware, conflict-free section choice
   (the engine picks sections honouring "no 8am / no Fridays"; the LLM never does).

No real DB/LLM — the resolver runs against a tiny fake session.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from keel.agent.graph import _after_stage
from keel.agent.tools.enrollment import (
    _fmt_slots,
    _resolve_sections_for_courses,
    _slots_meet_prefs,
    _verify_chosen_sections,
)

# Slot helpers ---------------------------------------------------------------

_MW_9 = [
    {"day": "mon", "start_min": 540, "end_min": 615},
    {"day": "wed", "start_min": 540, "end_min": 615},
]
_MW_8 = [
    {"day": "mon", "start_min": 480, "end_min": 555},
    {"day": "wed", "start_min": 480, "end_min": 555},
]
_FRI_1 = [{"day": "fri", "start_min": 780, "end_min": 900}]


def test_slots_meet_prefs_excluded_day() -> None:
    assert _slots_meet_prefs(_MW_9, {"fri"}, 0) is True
    assert _slots_meet_prefs(_FRI_1, {"fri"}, 0) is False


def test_slots_meet_prefs_min_start() -> None:
    # min_start_min = 540 (9:00 AM): an 8 AM section violates it.
    assert _slots_meet_prefs(_MW_9, set(), 540) is True
    assert _slots_meet_prefs(_MW_8, set(), 540) is False


def test_fmt_slots_readable() -> None:
    out = _fmt_slots(_MW_9)
    assert "Mon/Wed" in out
    assert "9:00 AM" in out
    assert _fmt_slots([]) == "TBA"


# _after_stage routing -------------------------------------------------------


def test_after_stage_pauses_on_staged_action() -> None:
    tc = [{"name": "stage_enrollment", "args": {}, "id": "1"}]
    state = {
        "messages": [
            AIMessage(content="", tool_calls=tc),
            ToolMessage(
                content=json.dumps({"action_id": "abc", "message": "approve?"}),
                tool_call_id="1",
            ),
        ]
    }
    assert _after_stage(state) == "interrupt"


def test_after_stage_returns_to_llm_on_error() -> None:
    err = json.dumps(
        {"error": "No open section for CS420.", "retryable": False, "category": "validation"}
    )
    tc = [{"name": "stage_enrollment", "args": {}, "id": "1"}]
    state = {
        "messages": [
            AIMessage(content="", tool_calls=tc),
            ToolMessage(content=err, tool_call_id="1"),
        ]
    }
    assert _after_stage(state) == "llm"


# Resolver -------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict]:
        return self._rows


class _FakeSession:
    """Returns the same rows for every execute() — enough for the resolver."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def execute(self, *_a: object, **_k: object) -> _FakeResult:
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_resolver_prefers_pref_meeting_section() -> None:
    rows = [
        {"id": "sec-8am", "course_code": "CS340", "slots": _MW_8, "instructor": "Dr. A"},
        {"id": "sec-9am", "course_code": "CS340", "slots": _MW_9, "instructor": "Dr. B"},
    ]
    chosen, unresolved = await _resolve_sections_for_courses(
        _FakeSession(rows),
        tenant_id="t",
        course_codes=["CS340"],
        term="spring",
        year=2026,
        min_start_hour=9,  # avoid 8 AM
    )
    assert unresolved == []
    assert len(chosen) == 1
    assert chosen[0]["section_id"] == "sec-9am"  # the 9 AM section, not the 8 AM
    assert chosen[0]["meets_prefs"] is True


@pytest.mark.asyncio
async def test_resolver_falls_back_when_no_pref_section() -> None:
    rows = [{"id": "sec-8am", "course_code": "CS340", "slots": _MW_8, "instructor": "Dr. A"}]
    chosen, unresolved = await _resolve_sections_for_courses(
        _FakeSession(rows),
        tenant_id="t",
        course_codes=["CS340"],
        term="spring",
        year=2026,
        min_start_hour=9,
    )
    # Only an 8 AM section exists → still resolves, but flagged as not meeting prefs.
    assert unresolved == []
    assert chosen[0]["section_id"] == "sec-8am"
    assert chosen[0]["meets_prefs"] is False


@pytest.mark.asyncio
async def test_resolver_avoids_time_conflict_across_courses() -> None:
    # Both courses' only sections meet at MW 9 → second course can't be placed.
    rows = [
        {"id": "a", "course_code": "CS340", "slots": _MW_9, "instructor": "Dr. A"},
        {"id": "b", "course_code": "CS402", "slots": _MW_9, "instructor": "Dr. B"},
    ]
    chosen, unresolved = await _resolve_sections_for_courses(
        _FakeSession(rows),
        tenant_id="t",
        course_codes=["CS340", "CS402"],
        term="spring",
        year=2026,
    )
    assert [c["course_code"] for c in chosen] == ["CS340"]
    assert unresolved == ["CS402"]


@pytest.mark.asyncio
async def test_resolver_unresolved_when_no_open_section() -> None:
    chosen, unresolved = await _resolve_sections_for_courses(
        _FakeSession([]),
        tenant_id="t",
        course_codes=["CS420"],
        term="spring",
        year=2026,
    )
    assert chosen == []
    assert unresolved == ["CS420"]


# _verify_chosen_sections (LLM-chosen sections → engine verifies) -------------


def _sec(course: str, slots: list[dict], enrolled: int = 0, capacity: int = 30) -> dict:
    return {
        "id": str(uuid4()),
        "course_code": course,
        "slots": slots,
        "instructor": "Dr. X",
        "enrolled": enrolled,
        "capacity": capacity,
    }


@pytest.mark.asyncio
async def test_verify_chosen_accepts_valid_combo() -> None:
    a = _sec("CS340", _MW_9)
    b = _sec("CS320", _FRI_1)  # different day → no conflict
    chosen, error = await _verify_chosen_sections(
        _FakeSession([a, b]),
        tenant_id="t",
        section_ids=[a["id"], b["id"]],
        course_codes=["CS340", "CS320"],
        term="spring",
        year=2026,
    )
    assert error is None
    assert {c["course_code"] for c in chosen} == {"CS340", "CS320"}


@pytest.mark.asyncio
async def test_verify_chosen_rejects_full_section() -> None:
    a = _sec("CS340", _MW_9, enrolled=30, capacity=30)
    chosen, error = await _verify_chosen_sections(
        _FakeSession([a]),
        tenant_id="t",
        section_ids=[a["id"]],
        course_codes=["CS340"],
        term="spring",
        year=2026,
    )
    assert chosen == []
    assert error is not None and "full" in error.lower()


@pytest.mark.asyncio
async def test_verify_chosen_rejects_wrong_course() -> None:
    # An id for a course not in the request → cannot enroll into an unrelated course.
    a = _sec("CS999", _MW_9)
    chosen, error = await _verify_chosen_sections(
        _FakeSession([a]),
        tenant_id="t",
        section_ids=[a["id"]],
        course_codes=["CS340"],
        term="spring",
        year=2026,
    )
    assert chosen == []
    assert error is not None and "CS999" in error


@pytest.mark.asyncio
async def test_verify_chosen_rejects_time_conflict() -> None:
    a = _sec("CS340", _MW_9)
    b = _sec("CS320", _MW_9)  # same slot → conflict
    chosen, error = await _verify_chosen_sections(
        _FakeSession([a, b]),
        tenant_id="t",
        section_ids=[a["id"], b["id"]],
        course_codes=["CS340", "CS320"],
        term="spring",
        year=2026,
    )
    assert chosen == []
    assert error is not None and "conflict" in error.lower()


@pytest.mark.asyncio
async def test_verify_chosen_rejects_unknown_id() -> None:
    chosen, error = await _verify_chosen_sections(
        _FakeSession([]),
        tenant_id="t",
        section_ids=[str(uuid4())],
        course_codes=["CS340"],
        term="spring",
        year=2026,
    )
    assert chosen == []
    assert error is not None and "not found" in error.lower()
