from datetime import UTC, datetime
from uuid import uuid4

from keel.domain.engine.contracts import (
    CoreRequirement,
    ElectiveGroupRequirement,
    Plan,
    PlanMeta,
    PlanTerm,
    Program,
)
from keel.domain.models import Course, Term
from keel.services.grad_plans import build_grad_plan_card


def _course(code: str, name: str, credits: int = 3) -> Course:
    return Course(
        tenant_id=uuid4(),
        code=code,
        name=name,
        credits=credits,
        difficulty=3,
        offered_terms=frozenset({Term.FALL, Term.SPRING}),
    )


def test_grad_plan_card_includes_course_names_and_requirement_labels() -> None:
    tenant_id = uuid4()
    student_id = uuid4()
    catalog = {
        "CS201": _course("CS201", "Data Structures"),
        "HIST110": _course("HIST110", "World History"),
    }
    program = Program(
        program_id=str(uuid4()),
        tenant_id=tenant_id,
        total_credits=120,
        requirements=[
            CoreRequirement(
                type="CORE",
                requirement_id="major_core",
                courses=["CS201"],
            ),
            ElectiveGroupRequirement(
                type="ELECTIVE_GROUP",
                requirement_id="general_electives",
                choose=1,
                from_courses=["HIST110"],
            ),
        ],
    )
    plan = Plan(
        plan_id=uuid4(),
        tenant_id=tenant_id,
        student_id=student_id,
        program_id=program.program_id,
        name="Balanced",
        version=1,
        active=True,
        terms=[
            PlanTerm(term=Term.FALL, year=2026, course_codes=["CS201", "HIST110"])
        ],
        meta=PlanMeta(generated_by="llm", created_at=datetime.now(UTC)),
    )

    card = build_grad_plan_card(
        plan,
        catalog,
        program,
        card_id="grad-1",
        label="Balanced",
        blurb="A steady load",
        status_by_term={("fall", 2026): "registered"},
    )

    term = card["terms"][0]
    assert term["termKey"] == "fall"
    assert term["year"] == 2026
    assert term["status"] == "registered"
    assert term["courses"] == [
        {
            "code": "CS201",
            "title": "Data Structures",
            "credits": 3,
            "requirement": "Major",
        },
        {
            "code": "HIST110",
            "title": "World History",
            "credits": 3,
            "requirement": "Elective",
        },
    ]
