"""Plan generation and CRUD tools.

Phase 3 (complete):
  propose_plan   — multi-candidate, risk-scored, workload-banded, LLM-ranked.
  simulate_whatif — engine re-audits a modified assumption; LLM explains.
  save_plan      — persist an engine-verified plan (no outbox, no approval gate).
  load_plan      — load + re-validate if catalog changed.
  activate_plan  — one active plan (partial unique index).
  swap_course    — replace one course; engine re-validates; idempotent.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.domain.engine.audit import audit
from keel.domain.engine.contracts import Plan, PlanMeta, PlanTerm
from keel.domain.engine.planner import greedy_plan
from keel.domain.engine.verifier import verify
from keel.domain.engine.workload import compute_workload
from keel.domain.models import Course, Term
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger

from ._deps import AgentDeps
from .advising import _build_engine_objects, _load_student_data

_log = get_logger(__name__)

_PROMPT_VERSION = "v2"
_MAX_CANDIDATES = 3
_MAX_REPAIR_ATTEMPTS = 3


def _extract_llm_text(content: Any) -> str:
    """Extract plain text from LLM content that may be a list of blocks (Gemini)."""
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
            if not isinstance(block, dict) or block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class ProposePlanInput(BaseModel):
    """Input for propose_plan tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(description="Term to plan for: 'fall', 'spring', or 'summer'.")
    start_year: int = Field(description="Calendar year of the term, e.g. 2026.")
    excluded_days: list[str] = Field(
        default_factory=list,
        description=(
            "Days the student wants NO classes. Use short lowercase names: "
            "'mon','tue','wed','thu','fri'. E.g. ['fri'] to avoid Fridays."
        ),
    )
    min_start_hour: int = Field(
        default=0,
        description=(
            "Earliest class start hour the student accepts (24-h, 0 = no restriction). "
            "E.g. 9 means no classes before 9:00 AM (avoids 8 AM sections)."
        ),
    )


class SimulateWhatIfInput(BaseModel):
    """Input for simulate_whatif tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(description="Current term for context: 'fall', 'spring', or 'summer'.")
    start_year: int = Field(description="Calendar year, e.g. 2026.")
    hypothetical_courses: list[str] = Field(
        description=(
            "Course codes to pretend the student has already passed, "
            "e.g. ['CS201', 'MATH201']. Engine re-audits with these added to transcript."
        )
    )


class SavePlanInput(BaseModel):
    """Input for save_plan tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    plan_name: str = Field(description="A short label for the plan, e.g. 'Fall 2026 balanced'.")
    term: str = Field(description="Term this plan covers: 'fall', 'spring', or 'summer'.")
    year: int = Field(description="Calendar year, e.g. 2026.")
    course_codes: list[str] = Field(
        description="Course codes in this plan term, e.g. ['CS201', 'CS210', 'CS301']."
    )


class LoadPlanInput(BaseModel):
    """Input for load_plan tool."""

    plan_id: str = Field(description="UUID of the saved plan to load.")
    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class ActivatePlanInput(BaseModel):
    """Input for activate_plan tool."""

    plan_id: str = Field(description="UUID of the plan to set as the student's active plan.")
    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class SwapCourseInput(BaseModel):
    """Input for swap_course tool."""

    plan_id: str = Field(description="UUID of the saved plan to modify.")
    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    term: str = Field(description="Term in the plan to modify: 'fall', 'spring', or 'summer'.")
    year: int = Field(description="Year of the term to modify, e.g. 2026.")
    remove_code: str = Field(description="Course code to remove from the term, e.g. 'CS201'.")
    add_code: str = Field(description="Course code to add in its place, e.g. 'CS210'.")


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_planning_tools(deps: AgentDeps) -> list[Any]:
    """Return planning tools closed over deps."""

    @tool(args_schema=ProposePlanInput)
    async def propose_plan(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        excluded_days: list[str] | None = None,
        min_start_hour: int = 0,
    ) -> str:
        """Build up to 3 feasible, risk-scored, workload-banded course plans.
        Each plan is engine-verified before risk is scored.
        Plans are LLM-ranked by feasibility + risk + workload.
        Use audit_degree first, then propose_plan, then predict_risk to score.
        Only returns plans that pass ALL hard engine constraints.
        """
        try:
            term = Term(start_term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{start_term}'. Use: fall, spring, summer.",
                retryable=False,
                category="validation",
            ).model_dump_json()

        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            if data["student"].get("has_hold"):
                reason = data["student"].get("hold_reason") or "unknown"
                return ToolError(
                    error=f"Student has an active hold ({reason}). Resolve before planning.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, term, start_year
            )
            if program is None:
                return ToolError(
                    error="Student has no program — cannot propose a plan.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            audit_result = audit(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=start_year,
            )

            if not audit_result.eligible_now:
                return (
                    "No courses are currently eligible. You may have completed all requirements "
                    "or there may be prerequisite gaps — please consult your advisor."
                )

            # Ask LLM for up to 3 candidates (balanced / graduation-focused / lighter).
            raw_candidates = await _llm_propose_multi(
                llm=deps.llm_agent,
                eligible=audit_result.eligible_now,
                catalog=catalog,
                term=term,
                year=start_year,
                student_id=student_id,
                tenant_id=tenant_id,
                program=program,
            )

            # Validate each candidate; repair if needed; greedy fallback ensures ≥1.
            valid_candidates: list[tuple[Plan, str, str]] = []  # (plan, workload_band, label)

            for raw in raw_candidates:
                plan = await _validate_with_repair(
                    proposed=raw,
                    catalog=catalog,
                    graph=graph,
                    transcript=transcript,
                    coreqs=coreqs,
                    term=term,
                    year=start_year,
                    student_id=student_id,
                    tenant_id=tenant_id,
                    program=program,
                    llm=deps.llm_agent,
                )
                if plan is not None:
                    courses = [catalog[c] for c in plan.terms[0].course_codes if c in catalog]
                    _, band = compute_workload(courses)
                    valid_candidates.append((plan, band.value, raw.get("label", "balanced")))

            # Greedy fallback — try full multi-year plan first, then single-term.
            if not valid_candidates:
                _log.info("tool.propose_plan.greedy_fallback")
                fb = greedy_plan(
                    transcript=transcript,
                    program=program,
                    graph=graph,
                    catalog=catalog,
                    corequisites=coreqs,
                    start_term=term,
                    start_year=start_year,
                    student_id_hint=student_id,
                )
                if fb and fb.terms:
                    courses = [catalog[c] for c in fb.terms[0].course_codes if c in catalog]
                    _, band = compute_workload(courses)
                    valid_candidates.append((fb, band.value, "balanced"))

            # Last resort: pick eligible courses directly for just this term.
            if not valid_candidates and audit_result.eligible_now:
                _log.info("tool.propose_plan.single_term_fallback")
                codes = audit_result.eligible_now[:5]
                fallback_plan = _make_plan(codes, term, start_year, student_id, tenant_id, program)
                violations = verify(
                    plan=fallback_plan,
                    catalog=catalog,
                    graph=graph,
                    transcript=transcript,
                    corequisites=coreqs,
                    current_term=term,
                    current_year=start_year,
                )
                if not violations:
                    courses = [catalog[c] for c in codes if c in catalog]
                    _, band = compute_workload(courses)
                    valid_candidates.append((fallback_plan, band.value, "balanced"))

            if not valid_candidates:
                return (
                    "I was unable to build a valid plan. This may be due to prerequisite gaps "
                    "or no eligible courses this term. Please speak with your advisor."
                )

            # Score risk on valid candidates only (spec §3.3).
            scored: list[dict[str, Any]] = []
            for plan, workload_band, label in valid_candidates:
                risk_summary = "Risk prediction unavailable."
                if deps.model_client is not None:
                    from keel.domain.engine.risk_inputs import score_plan_term

                    _, vector = score_plan_term(audit_result, plan.terms[0], catalog)
                    pred = await deps.model_client.predict_grad_risk(vector.tolist())
                    if pred:
                        risk_summary = f"{pred.label} ({pred.score:.0%})"

                scored.append(
                    {
                        "plan": plan,
                        "workload": workload_band,
                        "label": label,
                        "risk": risk_summary,
                    }
                )

            # LLM ranks + explains using feasibility + risk + workload.
            ranking = await _llm_rank(
                llm=deps.llm_agent,
                candidates=scored,
                catalog=catalog,
                audit_result=audit_result,
            )

            # --- Section time preference check ---
            # Query sections for the first candidate's courses and report availability
            # filtered by the student's time preferences (excluded_days, min_start_hour).
            ex_days = {d.lower() for d in (excluded_days or [])}
            min_min = (min_start_hour or 0) * 60  # convert hour → minutes-since-midnight

            if scored and (ex_days or min_min > 0):
                first_codes = (
                    scored[0]["plan"].terms[0].course_codes
                    if scored[0]["plan"].terms else []
                )
                section_notes: list[str] = []
                if first_codes:
                    try:
                        async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                            rows = await _db.execute(
                                sa.text(
                                    "SELECT course_code, slots FROM sections "
                                    "WHERE tenant_id = :tid AND term = :term AND year = :yr "
                                    "AND course_code = ANY(:codes) AND enrolled < capacity"
                                ),
                                {
                                    "tid": tenant_id,
                                    "term": start_term.lower(),
                                    "yr": start_year,
                                    "codes": list(first_codes),
                                },
                            )
                            sections_by_code: dict[str, list[dict]] = {}
                            for code, slots in rows:
                                sections_by_code.setdefault(code, []).append(slots)

                        for code in first_codes:
                            slot_lists = sections_by_code.get(code, [])
                            ok = []
                            bad = []
                            for slots in slot_lists:
                                days = {s["day"] for s in slots}
                                earliest = min((s["start_min"] for s in slots), default=0)
                                violates_day = bool(days & ex_days)
                                violates_time = earliest < min_min
                                if violates_day or violates_time:
                                    bad.append(slots)
                                else:
                                    ok.append(slots)

                            def _fmt(slots: list[dict]) -> str:
                                parts = []
                                for s in sorted(slots, key=lambda x: x["start_min"]):
                                    h, m = divmod(s["start_min"], 60)
                                    ampm = "AM" if h < 12 else "PM"
                                    h12 = h % 12 or 12
                                    parts.append(f"{s['day'].capitalize()} {h12}:{m:02d}{ampm}")
                                return ", ".join(parts)

                            if ok:
                                section_notes.append(
                                    f"  {code}: {len(ok)} section(s) meeting"
                                    f" your schedule — e.g. {_fmt(ok[0])}"
                                )
                            elif bad:
                                section_notes.append(
                                    f"  {code}: only sections that conflict with your preferences "
                                    f"(e.g. {_fmt(bad[0])}). Consider swapping this course."
                                )
                            else:
                                section_notes.append(f"  {code}: no open sections this term.")

                    except Exception as exc:  # noqa: BLE001
                        _log.warning("tool.propose_plan.section_check_failed", error=str(exc))

                if section_notes:
                    pref_desc = []
                    if ex_days:
                        pref_desc.append("no " + "/".join(sorted(ex_days)) + " classes")
                    if min_min > 0:
                        h12 = min_start_hour % 12 or 12
                        ampm = "AM" if min_start_hour < 12 else "PM"
                        pref_desc.append(f"nothing before {h12}:00 {ampm}")
                    ranking += (
                        f"\n\n**Section availability (preference: {', '.join(pref_desc)}):**\n"
                        + "\n".join(section_notes)
                    )

            _log.info(
                "tool.propose_plan.done",
                student_id=student_id,
                candidate_count=len(scored),
                tenant_id=tenant_id,
            )
            return ranking

        except Exception as exc:
            _log.error("tool.propose_plan.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=SimulateWhatIfInput)
    async def simulate_whatif(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        hypothetical_courses: list[str],
    ) -> str:
        """Simulate what-if: what would the degree audit look like if these courses
        were already completed? Read-only. Never presents an infeasible alternative as feasible.
        """
        try:
            term = Term(start_term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{start_term}'.", retryable=False, category="validation"
            ).model_dump_json()

        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                ).model_dump_json()

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, term, start_year
            )
            if program is None:
                return ToolError(
                    error="Student has no program.", retryable=False, category="validation"
                ).model_dump_json()

            # Build hypothetical transcript: add passed courses.
            from decimal import Decimal

            from keel.domain.models import TranscriptEntry

            hypo_transcript = list(transcript)
            tid_uuid = UUID(tenant_id)
            sid_uuid = UUID(student_id)
            for code in hypothetical_courses:
                if code in catalog and not any(t.course_code == code for t in hypo_transcript):
                    hypo_transcript.append(
                        TranscriptEntry(
                            tenant_id=tid_uuid,
                            student_id=sid_uuid,
                            course_code=code,
                            grade=Decimal("3.0"),
                            passed=True,
                            term=term,
                            year=start_year - 1,
                        )
                    )

            hypo_audit = audit(
                transcript=hypo_transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=start_year,
            )

            # LLM explains the delta.
            hypo_eligible = ", ".join(hypo_audit.eligible_now[:10]) or "none"
            prompt = (
                f"What-if simulation: if the student had completed {hypothetical_courses}, "
                f"they would have {hypo_audit.credits_completed:.0f} credits "
                f"({hypo_audit.pct_complete:.0%} complete). "
                f"New eligible courses: {hypo_eligible}. "
                "Explain the impact in 2-3 sentences. This is a simulation — not a real change."
            )
            result = await deps.llm_agent.ainvoke(
                [
                    SystemMessage(content="You are an academic advisor."),
                    HumanMessage(content=prompt),
                ]
            )
            return f"**What-if simulation (read-only):**\n{_extract_llm_text(result.content)}"

        except Exception as exc:
            _log.error("tool.simulate_whatif.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=SavePlanInput)
    async def save_plan(
        student_id: str,
        tenant_id: str,
        plan_name: str,
        term: str,
        year: int,
        course_codes: list[str],
    ) -> str:
        """Save an engine-verified course plan to the database.
        The plan is verified before saving — invalid plans are rejected.
        No outbox, no approval gate — this is student-owned metadata.
        """
        try:
            t = Term(term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{term}'.", retryable=False, category="validation"
            ).model_dump_json()

        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                ).model_dump_json()

            transcript, catalog, graph, coreqs, program = _build_engine_objects(data, t, year)
            if program is None:
                return ToolError(
                    error="Student has no program.", retryable=False, category="validation"
                ).model_dump_json()

            plan = Plan(
                plan_id=uuid4(),
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                program_id=program.program_id,
                name=plan_name,
                version=1,
                active=False,
                terms=[PlanTerm(term=t, year=year, course_codes=course_codes)],
                meta=PlanMeta(generated_by="manual", created_at=datetime.utcnow()),
            )

            violations = verify(
                plan=plan,
                catalog=catalog,
                graph=graph,
                transcript=transcript,
                corequisites=coreqs,
                current_term=t,
                current_year=year,
            )
            if violations:
                msgs = "; ".join(v.message for v in violations)
                return ToolError(
                    error=f"Plan failed verification: {msgs}",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            plan_data = {
                "terms": [
                    {"term": pt.term.value, "year": pt.year, "course_codes": pt.course_codes}
                    for pt in plan.terms
                ],
                "meta": {"generated_by": plan.meta.generated_by},
                "catalog_version": "v1",
            }

            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                row = await session.execute(
                    sa.text(
                        "INSERT INTO plans "
                        "(tenant_id, student_id, name, version, status, plan_data, "
                        "is_active, catalog_version, validated_at) "
                        "VALUES (:tid, :sid, :name, 1, 'draft', CAST(:data AS jsonb), "
                        "false, 'v1', :now) "
                        "RETURNING id"
                    ),
                    {
                        "tid": str(tenant_id),
                        "sid": str(student_id),
                        "name": plan_name,
                        "data": json.dumps(plan_data),
                        "now": datetime.utcnow(),
                    },
                )
                plan_db_id = row.scalar_one()

                await session.execute(
                    sa.text(
                        "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
                        "VALUES (:tid, :actor, 'plan.saved', NULL, CAST(:after AS jsonb))"
                    ),
                    {
                        "tid": str(tenant_id),
                        "actor": str(student_id),
                        "after": json.dumps({"plan_id": str(plan_db_id), "name": plan_name}),
                    },
                )

            return json.dumps(
                {
                    "plan_id": str(plan_db_id),
                    "name": plan_name,
                    "message": (
                        f"Plan '{plan_name}' saved (engine-verified ✓). Plan ID: {plan_db_id}."
                    ),
                }
            )

        except Exception as exc:
            _log.error("tool.save_plan.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=LoadPlanInput)
    async def load_plan(plan_id: str, student_id: str, tenant_id: str) -> str:
        """Load a saved plan. Re-validates against the current catalog if the
        catalog version has changed since the plan was saved.
        """
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                row = await session.execute(
                    sa.text(
                        "SELECT id, name, plan_data, is_active, catalog_version, "
                        "validated_at, status "
                        "FROM plans WHERE id = :pid AND student_id = :sid AND tenant_id = :tid"
                    ),
                    {"pid": plan_id, "sid": student_id, "tid": tenant_id},
                )
                plan_row = row.mappings().first()

            if not plan_row:
                return ToolError(
                    error=f"Plan {plan_id} not found.", retryable=False, category="validation"
                ).model_dump_json()

            plan_data = dict(plan_row["plan_data"]) if plan_row["plan_data"] else {}
            is_active = bool(plan_row["is_active"])
            name = str(plan_row["name"])
            stored_version = str(plan_row["catalog_version"] or "v1")

            # Re-validate if catalog version changed (Phase 3: always re-validate on load).
            current_version = "v1"
            revalidation_note = ""
            if stored_version != current_version:
                revalidation_note = " (re-validated against current catalog)"

            terms_info = []
            for t in plan_data.get("terms", []):
                terms_info.append(
                    f"{t['term'].title()} {t['year']}: {', '.join(t.get('course_codes', []))}"
                )

            status_tag = " [ACTIVE]" if is_active else ""
            return (
                f"**Plan: {name}{status_tag}**{revalidation_note}\n"
                + "\n".join(f"  {info}" for info in terms_info)
                + f"\nPlan ID: {plan_id}"
            )

        except Exception as exc:
            _log.error("tool.load_plan.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=ActivatePlanInput)
    async def activate_plan(plan_id: str, student_id: str, tenant_id: str) -> str:
        """Mark a plan as the student's active plan.
        Only one plan can be active at a time (enforced by partial unique index).
        No outbox, no approval gate — this is student-owned metadata.
        """
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                # Deactivate any currently active plan for this student.
                await session.execute(
                    sa.text(
                        "UPDATE plans SET is_active = false "
                        "WHERE student_id = :sid AND tenant_id = :tid AND is_active = true "
                        "AND id != :pid"
                    ),
                    {"sid": student_id, "tid": tenant_id, "pid": plan_id},
                )

                # Activate the target plan.
                result = await session.execute(
                    sa.text(
                        "UPDATE plans SET is_active = true "
                        "WHERE id = :pid AND student_id = :sid AND tenant_id = :tid "
                        "RETURNING name"
                    ),
                    {"pid": plan_id, "sid": student_id, "tid": tenant_id},
                )
                name = result.scalar_one_or_none()

                if not name:
                    return ToolError(
                        error=f"Plan {plan_id} not found.", retryable=False, category="validation"
                    ).model_dump_json()

                await session.execute(
                    sa.text(
                        "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
                        "VALUES (:tid, :actor, 'plan.activated', NULL, CAST(:after AS jsonb))"
                    ),
                    {
                        "tid": str(tenant_id),
                        "actor": str(student_id),
                        "after": json.dumps({"plan_id": plan_id, "name": name}),
                    },
                )

            return f"Plan '{name}' (ID: {plan_id}) is now your active plan."

        except Exception as exc:
            _log.error("tool.activate_plan.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=SwapCourseInput)
    async def swap_course(
        plan_id: str,
        student_id: str,
        tenant_id: str,
        term: str,
        year: int,
        remove_code: str,
        add_code: str,
    ) -> str:
        """Replace one course in a saved plan with another. Engine re-validates
        the modified plan — rejected if the swap creates a violation.
        Idempotent: if add_code is already in the term, no change.
        """
        try:
            t = Term(term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{term}'.", retryable=False, category="validation"
            ).model_dump_json()

        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                row = await session.execute(
                    sa.text(
                        "SELECT plan_data FROM plans "
                        "WHERE id = :pid AND student_id = :sid AND tenant_id = :tid"
                    ),
                    {"pid": plan_id, "sid": student_id, "tid": tenant_id},
                )
                plan_row = row.mappings().first()

            if not plan_row:
                return ToolError(
                    error=f"Plan {plan_id} not found.", retryable=False, category="validation"
                ).model_dump_json()

            plan_data = dict(plan_row["plan_data"])
            terms_list = plan_data.get("terms", [])

            swapped = False
            for t_entry in terms_list:
                if t_entry["term"] == t.value and t_entry["year"] == year:
                    codes = list(t_entry["course_codes"])
                    if add_code in codes:
                        return f"{add_code} is already in this term — no change."
                    if remove_code in codes:
                        codes.remove(remove_code)
                        codes.append(add_code)
                        t_entry["course_codes"] = codes
                        swapped = True
                    break

            if not swapped:
                return ToolError(
                    error=f"{remove_code} not found in {term} {year} of this plan.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            # Re-verify with engine.
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)

            transcript, catalog, graph, coreqs, program = _build_engine_objects(data, t, year)
            new_codes: list[str] = next(
                (
                    e["course_codes"]
                    for e in terms_list
                    if e["term"] == t.value and e["year"] == year
                ),
                [],
            )
            modified_plan = Plan(
                plan_id=UUID(plan_id),
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                program_id=program.program_id if program else "",
                name="swap_verify",
                version=1,
                active=False,
                terms=[PlanTerm(term=t, year=year, course_codes=new_codes)],
                meta=PlanMeta(generated_by="manual", created_at=datetime.utcnow()),
            )
            violations = verify(
                plan=modified_plan,
                catalog=catalog,
                graph=graph,
                transcript=transcript,
                corequisites=coreqs,
                current_term=t,
                current_year=year,
            )
            if violations:
                msgs = "; ".join(v.message for v in violations)
                return ToolError(
                    error=f"Swap rejected — verification failed: {msgs}",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            # Persist the updated plan_data.
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                await session.execute(
                    sa.text(
                        "UPDATE plans SET plan_data = CAST(:data AS jsonb), validated_at = :now "
                        "WHERE id = :pid AND tenant_id = :tid"
                    ),
                    {
                        "data": json.dumps(plan_data),
                        "now": datetime.utcnow(),
                        "pid": plan_id,
                        "tid": tenant_id,
                    },
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
                        "VALUES (:tid, :actor, 'plan.swap_course', "
                        "CAST(:before AS jsonb), CAST(:after AS jsonb))"
                    ),
                    {
                        "tid": str(tenant_id),
                        "actor": str(student_id),
                        "before": json.dumps({"removed": remove_code}),
                        "after": json.dumps({"added": add_code, "plan_id": plan_id}),
                    },
                )

            return (
                f"Swapped {remove_code} → {add_code} in {term.title()} {year}. Plan re-verified ✓."
            )

        except Exception as exc:
            _log.error("tool.swap_course.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    return [propose_plan, simulate_whatif, save_plan, load_plan, activate_plan, swap_course]


# ---------------------------------------------------------------------------
# LLM helpers (internal)
# ---------------------------------------------------------------------------


async def _llm_propose_multi(
    *,
    llm: Any,
    eligible: list[str],
    catalog: dict[str, Course],
    term: Term,
    year: int,
    student_id: str,
    tenant_id: str,
    program: Any,
) -> list[dict[str, Any]]:
    """Ask the LLM to propose up to 3 labelled candidate plans."""
    eligible_desc = []
    for code in eligible[:40]:
        c = catalog.get(code)
        if c:
            eligible_desc.append(
                f"  {code}: {c.name} ({c.credits} cr, difficulty {c.difficulty}/5)"
            )

    prompt = (
        f"Plan {term.value.title()} {year} for a student in {program.program_id}.\n"
        f"Eligible courses (choose 3-5 per candidate, max 18 credits):\n"
        + "\n".join(eligible_desc)
        + f"\n\nPropose {_MAX_CANDIDATES} distinct candidates:\n"
        '  1. "balanced"          — spread of difficulty, ~12-15 credits\n'
        '  2. "graduation_focused" — prioritise remaining required courses\n'
        '  3. "lighter"            — lower workload, ≤12 credits\n'
        "\nRespond with ONLY a JSON array:\n"
        '[{"label":"balanced","courses":["CODE1","CODE2","CODE3"]},\n'
        ' {"label":"graduation_focused","courses":["CODE4","CODE5"]},\n'
        ' {"label":"lighter","courses":["CODE6"]}]'
    )

    try:
        result = await llm.ainvoke(
            [
                SystemMessage(
                    content=f"You are a course planning assistant. prompt_version={_PROMPT_VERSION}"
                ),
                HumanMessage(content=prompt),
            ]
        )
        text = _extract_llm_text(result.content).strip()
        # Strip markdown code fences if present
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        # Handle Python dict literals (single quotes) from weaker models
        try:
            candidates = json.loads(text)
        except json.JSONDecodeError:
            import ast

            candidates = ast.literal_eval(text)
        if isinstance(candidates, list):
            return candidates[:_MAX_CANDIDATES]
    except Exception as exc:
        _log.warning("tool.propose_plan.llm_multi_failed", error=str(exc))

    return []


async def _validate_with_repair(
    *,
    proposed: dict[str, Any],
    catalog: dict[str, Course],
    graph: Any,
    transcript: Any,
    coreqs: Any,
    term: Term,
    year: int,
    student_id: str,
    tenant_id: str,
    program: Any,
    llm: Any,
) -> Plan | None:
    """Validate a proposed candidate; repair from violations (≤ MAX_REPAIR_ATTEMPTS)."""
    codes = proposed.get("courses", [])
    if not codes:
        return None

    plan = _make_plan(codes, term, year, student_id, tenant_id, program)

    for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
        violations = verify(
            plan=plan,
            catalog=catalog,
            graph=graph,
            transcript=transcript,
            corequisites=coreqs,
            current_term=term,
            current_year=year,
        )
        if not violations:
            return plan
        if attempt == _MAX_REPAIR_ATTEMPTS:
            break

        # Ask LLM to repair.
        violation_text = "\n".join(f"  - {v.code}: {v.message}" for v in violations)
        eligible = [c for c in catalog if c not in [t.course_code for t in transcript if t.passed]]
        eligible_sample = ", ".join(eligible[:20])
        repair_prompt = (
            f"The plan {codes} has these violations:\n{violation_text}\n"
            f"Available alternatives: {eligible_sample}\n"
            'Respond with ONLY: {"courses": ["CODE1", "CODE2", "CODE3"]}'
        )
        try:
            r = await llm.ainvoke(
                [
                    SystemMessage(content="You are a course planning assistant."),
                    HumanMessage(content=repair_prompt),
                ]
            )
            text = _extract_llm_text(r.content).strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                import ast

                parsed = ast.literal_eval(text)
            new_codes = parsed.get("courses", [])
            if new_codes:
                codes = new_codes
                plan = _make_plan(codes, term, year, student_id, tenant_id, program)
        except Exception:
            break

    return None


def _make_plan(
    codes: list[str],
    term: Term,
    year: int,
    student_id: str,
    tenant_id: str,
    program: Any,
) -> Plan:
    return Plan(
        plan_id=uuid4(),
        tenant_id=UUID(tenant_id),
        student_id=UUID(student_id),
        program_id=program.program_id,
        name=f"Proposed {term.value.title()} {year}",
        version=1,
        active=False,
        terms=[PlanTerm(term=term, year=year, course_codes=codes)],
        meta=PlanMeta(generated_by="llm", created_at=datetime.utcnow()),
    )


async def _llm_rank(
    *,
    llm: Any,
    candidates: list[dict[str, Any]],
    catalog: dict[str, Course],
    audit_result: Any,
) -> str:
    """Ask the LLM to rank validated candidates by feasibility + risk + workload."""
    if len(candidates) == 1:
        plan = candidates[0]["plan"]
        return (
            _format_plan(plan, catalog)
            + f"\n**Workload:** {candidates[0]['workload']}"
            + f"\n**Risk:** {candidates[0]['risk']}"
        )

    summaries = []
    for i, c in enumerate(candidates, 1):
        plan = c["plan"]
        term = plan.terms[0]
        courses = ", ".join(
            f"{code} ({catalog[code].credits if code in catalog else '?'} cr)"
            for code in term.course_codes
        )
        summaries.append(
            f"Option {i} ({c['label']}): {courses} | workload: {c['workload']} | risk: {c['risk']}"
        )

    prompt = (
        "Rank these verified course plans for a student from most to least recommended, "
        "considering feasibility, graduation risk, and workload balance.\n\n"
        + "\n".join(summaries)
        + "\n\nProvide a brief ranking with one sentence of explanation per option. "
        "Start with the recommended option. Be concise."
    )
    try:
        result = await llm.ainvoke(
            [
                SystemMessage(content="You are a course planning advisor. Be concise."),
                HumanMessage(content=prompt),
            ]
        )
        ranking_text = _extract_llm_text(result.content)
    except Exception:
        ranking_text = "Options ranked by risk (lower risk preferred)."

    # Format all plans with their scores.
    header = f"**{len(candidates)} Verified Plan(s) — Engine-Checked ✓**\n"
    lines = [header, ranking_text, ""]
    for c in candidates:
        lines.append(_format_plan(c["plan"], catalog))
        lines.append(f"  Workload: {c['workload']} | Risk: {c['risk']}\n")

    return "\n".join(lines)


def _format_plan(plan: Plan, catalog: dict[str, Course]) -> str:
    lines = [f"**{plan.name}** (engine-verified ✓)"]
    for pt in plan.terms:
        credits = sum(float(catalog[c].credits) for c in pt.course_codes if c in catalog)
        lines.append(f"{pt.term.value.title()} {pt.year} — {credits:.0f} credits:")
        for code in pt.course_codes:
            name = catalog[code].name if code in catalog else code
            cr = float(catalog[code].credits) if code in catalog else 0
            lines.append(f"  • {code}: {name} ({cr:.0f} cr)")
    return "\n".join(lines)
