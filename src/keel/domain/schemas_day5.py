"""Day-5 typed boundaries (spec Appendix A) — advising, guidance, institutional.

These are the In/Out Pydantic models for the C/E/F tools. Like ``schemas.py`` this
module is import-clean: no I/O, no framework imports, no engine imports. The few
engine-shaped types below (``Violation``, ``CourseRef``, ``ValidatedPlan``,
``AuditResult``, ``EngineImpact``) are standalone re-statements for the tool
boundary — the tools translate engine value objects into these before returning.

Invariant: ``ValidatedPlan`` is only ever constructed from a plan the engine
verifier has already accepted (``is_valid`` is a type-level ``True``). No tool may
build one from an unverified course list.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ---------- shared engine-shaped boundary types ----------


class Violation(BaseModel):
    code: Literal[
        "PREREQ_MISSING",
        "TIME_CONFLICT",
        "CAPACITY_FULL",
        "CREDIT_CAP",
        "COREQ_MISSING",
        "HOLD",
        "REPEAT_PASSED",
        "OFFERING_TERM",
    ]
    course_code: str
    detail: str


class CourseRef(BaseModel):
    code: str
    name: str
    credits: int


class ValidatedPlan(BaseModel):
    """A plan that PASSED the verifier. Only the engine constructs this."""

    plan_id: UUID | None = None  # set once saved via save_plan
    terms: list[list[CourseRef]]  # courses per term, in order
    total_credits: int
    workload_band: Literal["light", "medium", "heavy"]
    is_valid: Literal[True] = True  # type-level guarantee


class AuditResult(BaseModel):
    remaining_requirements: list[str]
    remaining_credits: int
    eligible_courses: list[CourseRef]


class EngineImpact(BaseModel):
    """Deterministic consequence of a failure / major switch."""

    delayed_courses: list[str]
    new_graduation_term: str
    lost_credits: int = 0
    extra_terms: int = 0


# ---------- C1: Course Advisor ----------


class CourseAdvisorIn(BaseModel):
    query: str = Field(..., min_length=1)
    tenant_id: UUID
    student_id: UUID


class CourseAdvisorOut(BaseModel):
    answer: str
    sources: list[str]
    prereqs_from_dag: list[str]  # injected from engine, NOT prose


# ---------- C2: Degree Audit Chat ----------


class DegreeAuditChatIn(BaseModel):
    tenant_id: UUID
    student_id: UUID


class DegreeAuditChatOut(BaseModel):
    audit: AuditResult  # engine numbers, verbatim
    narrative: str


# ---------- C3: Failure Recovery (propose-verify-repair) ----------


class FailureRecoveryIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    failed_course: str


class FailureRecoveryOut(BaseModel):
    impact: EngineImpact
    recovery_plan: ValidatedPlan  # produced by the loop, verifier-valid
    narrative: str


# ---------- C4: Major-Switch Advisor ----------


class MajorSwitchAdviceIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    target_program: str


class MajorSwitchAdviceOut(BaseModel):
    consequences: EngineImpact
    recommendation: str
    caveat: str = "Advisory only — not a guarantee."


# ---------- E1: Elective Recommender ----------


class ElectivePrefs(BaseModel):
    gpa_goal: float | None = None
    difficulty: Literal["easier", "balanced", "challenging"] = "balanced"
    career_direction: str | None = None


class ElectiveRecommenderIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    prefs: ElectivePrefs = ElectivePrefs()


class RankedElective(BaseModel):
    course: CourseRef
    reason: str


class ElectiveRecommenderOut(BaseModel):
    ranked_electives: list[RankedElective]  # all ∈ engine-eligible set


# ---------- E2: Career Path (advice + verified save) ----------


class CareerPathIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    interest: str = Field(..., min_length=1)


class CareerPathOut(BaseModel):
    direction: str
    suggested_courses: list[CourseRef]  # must exist in catalog
    skills: list[str]
    caveat: str = "This is a grounded suggestion, not a prediction."


class SaveCareerRoadmapIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    interest: str
    plan_name: str = "Career-aligned"


class SaveCareerRoadmapOut(BaseModel):
    saved_plan: ValidatedPlan  # verifier-valid, persisted


# ---------- F: Institutional Requests ----------

RequestType = Literal[
    "GRADUATION_APPLICATION",
    "MAJOR_CHANGE",
    "PETITION",
]


class RequestQueueRow(BaseModel):
    id: UUID
    tenant_id: UUID
    student_id: UUID
    request_type: RequestType
    target: str  # program_id, course_code, etc.
    status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING"
    idempotency_key: str


class ApplyGraduationIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    approved: bool = False  # MUST be True to write (see CI test)


class RequestMajorChangeIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    target_program_id: str
    approved: bool = False


class SubmitPetitionIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    course_id: str
    justification: str = Field(..., min_length=1)
    approved: bool = False


# ---------- F4: Escalation ----------


class EscalationIn(BaseModel):
    tenant_id: UUID
    student_id: UUID
    reason: str


class EscalationOut(BaseModel):
    advisor_email: str  # resolved from advisors table
    handoff_summary: str
    sent: bool
