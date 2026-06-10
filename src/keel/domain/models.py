"""Core domain value objects — the shared vocabulary (SPEC.md §1).

Pure Pydantic v2 models, ``frozen=True`` where they are value objects. This
module imports nothing from ``infra``/``services`` and performs no I/O. These
are SEPARATE from the SQLAlchemy ORM models in ``infra.database.models`` — repositories map
between the two.

Phase 0 ships the catalog/identity vocabulary used by the engine in later
phases. Plan-entity and validator types are added when those phases land.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class Term(StrEnum):
    FALL = "fall"
    SPRING = "spring"
    SUMMER = "summer"


class DayOfWeek(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class TimeSlot(BaseModel):
    """A meeting time. Invariant: 0 <= start_min < end_min <= 1439."""

    model_config = {"frozen": True}

    day: DayOfWeek
    start_min: int = Field(ge=0, le=1439)
    end_min: int = Field(ge=0, le=1439)

    @model_validator(mode="after")
    def _check_order(self) -> TimeSlot:
        if self.start_min >= self.end_min:
            raise ValueError("start_min must be strictly before end_min")
        return self

    def overlaps(self, other: TimeSlot) -> bool:
        """True if the two slots conflict.

        Touching/adjacent slots (one ends exactly when the other begins) do
        NOT overlap. Different days never overlap.
        """
        if self.day != other.day:
            return False
        return self.start_min < other.end_min and other.start_min < self.end_min


class Course(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    code: str
    name: str
    credits: int = Field(gt=0)
    difficulty: int = Field(ge=1, le=5)
    offered_terms: frozenset[Term]


class Section(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    id: UUID
    course_code: str
    term: Term
    year: int
    slots: tuple[TimeSlot, ...]
    capacity: int = Field(ge=0)
    enrolled: int = Field(ge=0)

    @property
    def is_open(self) -> bool:
        return self.enrolled < self.capacity


class Prerequisite(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    course_code: str
    requires_code: str
    min_grade: Decimal | None = None


class Corequisite(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    course_code: str
    coreq_code: str


class TranscriptEntry(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    student_id: UUID
    course_code: str
    term: Term
    year: int
    grade: Decimal | None = None  # None = in progress
    passed: bool = False


class Hold(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    student_id: UUID
    kind: str
    blocks_registration: bool


class ProgramRequirement(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    program_code: str
    group_name: str
    required_credits: int = Field(ge=0)
    eligible_course_codes: frozenset[str]


class Student(BaseModel):
    model_config = {"frozen": True}

    tenant_id: UUID
    id: UUID
    program_code: str
    max_credits_per_term: int = Field(gt=0)
    current_term: Term
    current_year: int
