"""Mapper: raw sections row -> engine Section object. Pure, moved verbatim."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from keel.domain.models import DayOfWeek, Section, Term, TimeSlot


def _row_to_section(row: dict[str, Any], term: Term, year: int, tenant_id: UUID) -> Section:
    """Build an engine Section domain object from a raw sections row."""
    slots = tuple(
        TimeSlot(
            day=DayOfWeek(str(s["day"]).lower()),
            start_min=int(s["start_min"]),
            end_min=int(s["end_min"]),
        )
        for s in (row["slots"] or [])
    )
    sec_id = row["id"] if isinstance(row["id"], UUID) else UUID(str(row["id"]))
    return Section(
        tenant_id=tenant_id,
        id=sec_id,
        course_code=row["course_code"],
        term=term,
        year=year,
        slots=slots,
        capacity=int(row["capacity"]),
        enrolled=int(row["enrolled"]),
    )
