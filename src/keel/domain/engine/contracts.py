"""Engine contracts: Plan, Violation, AuditResult, Requirement, Program.

These are the frozen value objects that flow between engine components and
callers. The engine imports from here; everything above (services, API) reads
these types. Nothing here imports from infra, services, or api.

Source of truth: specs/004-phase-1-engine/spec.md §4.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from keel.domain.models import Term

# ── Violation ─────────────────────────────────────────────────────────────────


class ViolationCode(StrEnum):
    PREREQ_MISSING = "PREREQ_MISSING"
    CREDIT_CAP_EXCEEDED = "CREDIT_CAP_EXCEEDED"
    COREQ_MISSING = "COREQ_MISSING"
    REPEAT_PASSED = "REPEAT_PASSED"
    NOT_OFFERED_THIS_TERM = "NOT_OFFERED_THIS_TERM"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    UNKNOWN_COURSE = "UNKNOWN_COURSE"
    TIME_CONFLICT = "TIME_CONFLICT"
    CAPACITY_FULL = "CAPACITY_FULL"


class ViolationScope(StrEnum):
    COURSE = "course"
    SECTION = "section"


class Violation(BaseModel):
    """A single constraint violation returned by the verifier.

    The repair loop keys off ``code`` + ``detail`` (machine-readable).
    ``message`` is for the UI only.
    Empty violation list from the verifier means the plan is valid.
    """

    model_config = {"frozen": True}

    code: ViolationCode
    scope: ViolationScope
    term: Term | None = None
    year: int | None = None
    courses: list[str]
    detail: dict[str, Any]
    message: str


# ── Plan ──────────────────────────────────────────────────────────────────────


class PlanTerm(BaseModel):
    """One term slot in a multi-term plan. Sections are not stored here —
    they are resolved at registration time."""

    model_config = {"frozen": True}

    term: Term
    year: int
    course_codes: list[str]


class PlanMeta(BaseModel):
    model_config = {"frozen": True}

    generated_by: Literal["llm", "greedy", "manual"]
    created_at: datetime


class Plan(BaseModel):
    """The Plan entity — a multi-term sequence of courses.

    Sections are resolved at registration; the plan stores courses per term
    so catalog / section churn doesn't invalidate saved plans.
    """

    model_config = {"frozen": True}

    plan_id: UUID
    tenant_id: UUID
    student_id: UUID
    program_id: str
    name: str
    version: int = Field(ge=1)
    active: bool
    terms: list[PlanTerm]
    meta: PlanMeta


# ── Requirement types (spec §4.6) ─────────────────────────────────────────────


class CoreRequirement(BaseModel):
    model_config = {"frozen": True}

    type: Literal["CORE"]
    requirement_id: str
    courses: list[str]


class ElectiveGroupRequirement(BaseModel):
    model_config = {"frozen": True}

    type: Literal["ELECTIVE_GROUP"]
    requirement_id: str
    choose: int = Field(ge=1)
    from_courses: list[str]


class CreditFloorRequirement(BaseModel):
    model_config = {"frozen": True}

    type: Literal["CREDIT_FLOOR"]
    requirement_id: str
    category: str
    min_credits: int = Field(ge=0)


Requirement = Annotated[
    CoreRequirement | ElectiveGroupRequirement | CreditFloorRequirement,
    Field(discriminator="type"),
]


class Program(BaseModel):
    """A degree program — the input to the degree audit."""

    model_config = {"frozen": True}

    program_id: str
    tenant_id: UUID
    total_credits: int = Field(ge=0)
    requirements: list[Requirement]


# ── AuditResult ───────────────────────────────────────────────────────────────


class CompletedRequirement(BaseModel):
    model_config = {"frozen": True}

    requirement_id: str
    satisfied_by: list[str]


class RemainingRequirement(BaseModel):
    model_config = {"frozen": True}

    requirement_id: str
    type: Literal["CORE", "ELECTIVE_GROUP", "CREDIT_FLOOR"]
    still_needed: float  # credits for CREDIT_FLOOR; course count for others


class AuditResult(BaseModel):
    """Output of the degree audit (spec §4.3).

    The student-state fields (cumulative_gpa … num_repeats) are the exact
    values that go into RawFeatureInputs in grad_risk.py — no second copy
    of the formulas anywhere.
    """

    model_config = {"frozen": True}

    completed_requirements: list[CompletedRequirement]
    remaining_requirements: list[RemainingRequirement]

    credits_completed: float = Field(ge=0.0)
    total_credits_required: float = Field(ge=0.0)
    remaining_credits: float
    pct_complete: float = Field(ge=0.0, le=1.0)
    progress_rate: float = Field(ge=0.0)
    terms_elapsed: int = Field(ge=0)

    # Courses eligible right now: prereqs met + offered this term + not passed + in program
    eligible_now: list[str]

    # Student-state metrics — passed straight into RawFeatureInputs; engine owns assembly
    cumulative_gpa: float = Field(ge=0.0, le=4.0)
    recent_term_gpas: list[float]
    num_failures: int = Field(ge=0)
    num_repeats: int = Field(ge=0)
