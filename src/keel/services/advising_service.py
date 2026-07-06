"""Advising application services: degree audit, RAG search, risk prediction, GPA
estimate, account facts, and the C1-C4 advising-chat use cases.

Extracted from the advising agent tools; behaviour unchanged. Infra collaborators
are passed explicitly; the student load goes straight to the repository.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.agent.llm.advising_llm import _extract_advise_text, _llm_mitigation
from keel.domain.engine.audit import audit
from keel.domain.models import Course, Term, TranscriptEntry
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.infra.rag import retrieve
from keel.logging import get_logger
from keel.mappers.engine_context import _build_engine_objects, _build_requirement
from keel.presenters.risk import _build_risk_reasons
from keel.repositories.programs import ProgramRepository
from keel.repositories.students import StudentRepository

_log = get_logger(__name__)

_COURSE_CODE_RE = re.compile(r"[A-Z]{2,4}\d{3}[A-Z]?")


async def _load_program_engine(
    session: AsyncSession,
    tenant_id: str,
    program_code: str,
    catalog: dict[str, Course],
) -> Any:
    """Load a program (by code) as an engine Program object, or None if absent.

    Used by C4 / F2 to audit a student against a *target* program they are not
    currently enrolled in.
    """
    from keel.domain.engine.contracts import CoreRequirement, Program

    repo = ProgramRepository(session, UUID(tenant_id))
    pr = await repo.get_by_code(program_code)
    if not pr:
        return None
    req_rows = await repo.requirements_for(pr["id"])
    reqs = []
    for rr in req_rows:
        codes = rr["eligible_course_codes"]
        if isinstance(codes, str):
            codes = json.loads(codes)
        reqs.append(
            _build_requirement(
                group_name=rr["group_name"],
                codes=list(codes),
                required_credits=rr.get("required_credits"),
                catalog=catalog,
            )
        )
    if not reqs:
        reqs = [CoreRequirement(type="CORE", requirement_id="all", courses=list(catalog.keys()))]
    return Program(
        program_id=str(pr["id"]),
        tenant_id=UUID(str(pr["tenant_id"])),
        total_credits=int(pr.get("total_credits_required", 120)),
        requirements=reqs,
    )


def _term_after(term: Term, year: int, n_terms: int) -> str:
    """Advance (term, year) by n_terms over a fall↔spring cadence; return a label.

    Standard calendar: within year Y, Spring precedes Fall; Fall <Y> → Spring <Y+1>.
    Absolute semester index: Spring Y = 2Y, Fall Y = 2Y+1 (start-agnostic).
    """
    base = 2 * year + (1 if term == Term.FALL else 0)
    nxt = base + n_terms
    new_term = Term.FALL if nxt % 2 == 1 else Term.SPRING
    new_year = nxt // 2
    return f"{new_term.value.title()} {new_year}"


def _compute_switch_impact(
    *,
    transcript: list[TranscriptEntry],
    catalog: dict[str, Course],
    target_program: Any,
    target_audit: Any,
    current_term: Term,
    current_year: int,
) -> dict[str, Any]:
    """Deterministic consequences of switching to ``target_program`` (spec C4/F2).

    lost_credits = passed credits that don't count toward the target program.
    extra_terms  = remaining target credits at ~15/term.
    delayed_courses = target requirements not yet eligible.
    """
    target_codes: set[str] = set()
    for req in target_program.requirements:
        target_codes.update(getattr(req, "courses", []) or [])

    lost_credits = 0
    for t in transcript:
        if t.passed and t.course_code not in target_codes and t.course_code in catalog:
            lost_credits += int(catalog[t.course_code].credits)

    remaining = int(getattr(target_audit, "remaining_credits", 0) or 0)
    extra_terms = max(0, -(-remaining // 15))  # ceil
    delayed = [c for c in target_codes if c not in set(target_audit.eligible_now)][:8]

    return {
        "lost_credits": lost_credits,
        "extra_terms": extra_terms,
        "new_graduation_term": _term_after(current_term, current_year, extra_terms),
        "delayed_courses": delayed,
    }


async def audit_degree(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    student_id: str,
    tenant_id: str,
) -> str:
    """Extracted from the audit_degree advising tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            err = ToolError(
                error=f"Student {student_id} not found in tenant {tenant_id}",
                retryable=False,
                category="validation",
            )
            return err.model_dump_json()

        transcript, catalog, graph, coreqs, program = _build_engine_objects(
            data, current_term, current_year
        )
        if program is None:
            err = ToolError(
                error="Student has no program assigned — cannot audit.",
                retryable=False,
                category="validation",
            )
            return err.model_dump_json()

        result = audit(
            transcript=transcript,
            program=program,
            graph=graph,
            catalog=catalog,
            current_term=current_term,
            current_year=current_year,
        )

        eligible_list = ", ".join(result.eligible_now) or "none"
        remaining = [r.requirement_id for r in result.remaining_requirements]

        summary = (
            f"Current term: {current_term.value}, year: {current_year}.\n"
            f"Credits completed: {result.credits_completed:.1f} / "
            f"{result.total_credits_required:.1f} "
            f"({result.pct_complete * 100:.0f}%).\n"
            f"Remaining requirement groups: {', '.join(remaining) or 'none'}.\n"
            f"Courses eligible now: {eligible_list}.\n"
            f"GPA: {result.cumulative_gpa:.2f}."
        )
        if data["student"].get("has_hold"):
            reason = data["student"].get("hold_reason") or "unknown"
            summary += f"\n⚠ Student has an active hold: {reason}."

        _log.info(
            "tool.audit_degree.done",
            student_id=student_id,
            eligible_count=len(result.eligible_now),
        )
        return summary

    except Exception as exc:
        _log.error("tool.audit_degree.error", error=str(exc))
        err = ToolError(error=str(exc), retryable=True, category="engine")
        return err.model_dump_json()


async def rag_search(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    cohere_client: Any,
    settings: Any,
    tenant_id: str,
    query: Any,
) -> str:
    """Extracted from the rag_search advising tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            results = await retrieve(
                query=query,
                tenant_id=tenant_id,
                session=_db,
                cohere_client=cohere_client,
                settings=settings,
            )
        if not results:
            _log.info("tool.rag_search.empty", query_snippet=query[:60])
            return "No relevant documents found for this query."

        parts = []
        for i, r in enumerate(results, start=1):
            label = r.code or r.doc or r.source
            parts.append(f"[{i}] {label}: {r.content}")
        return "\n\n".join(parts)

    except Exception as exc:
        _log.error("tool.rag_search.error", error=str(exc))
        err = ToolError(error=str(exc), retryable=True, category="external")
        return err.model_dump_json()


async def predict_risk(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    model_client: Any,
    llm: Any,
    student_id: str,
    tenant_id: str,
    start_term: Any,
    start_year: Any,
    course_codes: Any,
) -> str:
    """Extracted from the predict_risk advising tool (behaviour unchanged)."""
    try:
        from keel.domain.models import Term as _Term

        try:
            term = _Term(start_term.lower())
        except ValueError:
            err = ToolError(
                error=f"Invalid term '{start_term}'.", retryable=False, category="validation"
            )
            return err.model_dump_json()

        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            err = ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            )
            return err.model_dump_json()

        transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, start_year)
        if program is None:
            err = ToolError(error="Student has no program.", retryable=False, category="validation")
            return err.model_dump_json()

        from keel.domain.engine.audit import audit as _audit
        from keel.domain.engine.contracts import PlanTerm
        from keel.domain.engine.risk_inputs import score_plan_term

        audit_result = _audit(
            transcript=transcript,
            program=program,
            graph=graph,
            catalog=catalog,
            current_term=term,
            current_year=start_year,
        )

        plan_term = PlanTerm(term=term, year=start_year, course_codes=course_codes)
        features, vector = score_plan_term(audit_result, plan_term, catalog)

        # Call model-server (shared ModelClient).
        prediction = None
        if model_client is not None:
            prediction = await model_client.predict_grad_risk(vector.tolist())

        if prediction is None:
            return (
                "Risk prediction is temporarily unavailable. "
                "Your plan has been verified by the engine — please consult your advisor "
                "for a risk assessment."
            )

        # Deterministic reasons from feature values (spec §3.2 — not LLM-invented).
        reasons = _build_risk_reasons(features, prediction.label)

        # LLM writes only the mitigation plan from those reasons.
        mitigation = await _llm_mitigation(
            llm=llm,
            label=prediction.label,
            score=prediction.score,
            reasons=reasons,
            course_codes=course_codes,
        )

        _log.info(
            "tool.predict_risk.done",
            student_id=student_id,
            label=prediction.label,
            score=round(prediction.score, 3),
            tenant_id=tenant_id,
        )
        return (
            f"**Risk Assessment** — {prediction.label.upper()} "
            f"(confidence: {prediction.score:.0%})\n\n"
            f"**Key factors:**\n{reasons}\n\n"
            f"**Mitigation:**\n{mitigation}"
        )

    except Exception as exc:
        _log.error("tool.predict_risk.error", error=str(exc))
        err = ToolError(error=str(exc), retryable=True, category="engine")
        return err.model_dump_json()


async def gpa_estimate(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    llm: Any,
    student_id: str,
    tenant_id: str,
    course_codes: Any,
) -> str:
    """Extracted from the gpa_estimate advising tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            err = ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            )
            return err.model_dump_json()

        from keel.domain.models import Term as _Term

        transcript, catalog, _, _, _ = _build_engine_objects(data, _Term.FALL, 2025)

        # Compute current GPA from transcript.
        grades = [t.grade for t in transcript if t.grade is not None]
        current_gpa = float(sum(grades) / len(grades)) if grades else 0.0

        course_details = [
            f"{c}: {catalog[c].name} (difficulty {catalog[c].difficulty}/5)"
            for c in course_codes
            if c in catalog
        ]
        course_list = "\n".join(course_details) or "No recognized courses."

        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = (
            f"A student has a current cumulative GPA of {current_gpa:.2f}. "
            f"They are considering these courses:\n{course_list}\n\n"
            "Give a brief, realistic GPA estimate for this term load. "
            "You MUST include the caveat: 'This is an estimate only, not a prediction.' "
            "Keep the response under 3 sentences."
        )
        result = await llm.ainvoke(
            [
                SystemMessage(content="You are an academic advisor giving a rough GPA estimate."),  # noqa: E501
                HumanMessage(content=prompt),
            ]
        )
        return f"**GPA Estimate (estimate only, not a prediction):** {result.content}"

    except Exception as exc:
        _log.error("tool.gpa_estimate.error", error=str(exc))
        err = ToolError(error=str(exc), retryable=True, category="engine")
        return err.model_dump_json()


async def my_info(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
) -> str:
    """Extracted from the my_info advising tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            repo = StudentRepository(session, UUID(tenant_id))
            info = await repo.get_account_info(student_id)
            if not info:
                return ToolError(
                    error="Student not found.", retryable=False, category="validation"
                ).model_dump_json()
            a: dict[str, Any] = await repo.get_transcript_aggregate(student_id)
        gpa = a.get("gpa")
        lines = [
            f"Major / program: {info['program_name'] or 'Undeclared'} "
            f"({info['program_code'] or '—'})",
            f"Current term: {str(info['current_term']).title()} {info['current_year']}",
            f"GPA: {gpa if gpa is not None else 'N/A'}",
            f"Completed credits: {int(a.get('cc') or 0)}",
            f"Courses failed: {int(a.get('failed') or 0)}",
        ]
        if info["has_hold"]:
            lines.append(
                f"⚠ Active hold: {info['hold_reason'] or 'unspecified'} (blocks registration)"
            )
        else:
            lines.append("Holds: none")
        return "Here are your account details:\n" + "\n".join(f"- {ln}" for ln in lines)
    except Exception as exc:
        _log.error("tool.my_info.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def course_advisor(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    cohere_client: Any,
    settings: Any,
    llm: Any,
    student_id: str,
    tenant_id: str,
    query: Any,
) -> str:
    """Extracted from the course_advisor advising tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            results = await retrieve(
                query=query,
                tenant_id=tenant_id,
                session=_db,
                cohere_client=cohere_client,
                settings=settings,
            )
            data = await StudentRepository(_db, tenant_id).load_context(student_id)

        # Prereqs from the DAG (authoritative), keyed off any course codes in the query.
        dag_prereqs: list[str] = []
        codes_in_query = set(_COURSE_CODE_RE.findall(query.upper()))
        for r in data.get("prereq_rows", []):
            if r["course_code"] in codes_in_query:
                dag_prereqs.append(f"{r['course_code']} requires {r['requires_code']}")

        context = (
            "\n\n".join(
                f"[{i}] {(r.code or r.doc or r.source)}: {r.content}"
                for i, r in enumerate(results, 1)
            )
            or "No catalog context found."
        )
        sources = [r.code or r.doc or r.source for r in results]

        from langchain_core.messages import HumanMessage, SystemMessage

        prereq_note = (
            "\n\nAUTHORITATIVE PREREQUISITES (from the engine DAG — use these, "
            "not any prereqs in the context): " + "; ".join(dag_prereqs)
            if dag_prereqs
            else ""
        )
        prompt = (
            f"Answer the student's question using ONLY the catalog context below.\n"
            f"QUESTION: {query}\n\nCONTEXT:\n{context}{prereq_note}\n\n"
            "Be specific and complete. If prerequisites are stated above, use them verbatim."
        )
        res = await llm.ainvoke(
            [
                SystemMessage(content="You are a course advisor. Ground every claim in context."),
                HumanMessage(content=prompt),
            ]
        )
        answer = _extract_advise_text(res.content)
        out = answer
        if dag_prereqs:
            out += "\n\n**Prerequisites (from records):** " + "; ".join(dag_prereqs)
        if sources:
            out += "\n\n_Sources: " + ", ".join(dict.fromkeys(sources)) + "_"
        return out
    except Exception as exc:
        _log.error("tool.course_advisor.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="external").model_dump_json()


async def degree_audit_chat(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    llm: Any,
    student_id: str,
    tenant_id: str,
) -> str:
    """Extracted from the degree_audit_chat advising tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            return ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            ).model_dump_json()
        transcript, catalog, graph, coreqs, program = _build_engine_objects(
            data, current_term, current_year
        )
        if program is None:
            return ToolError(
                error="Student has no program.", retryable=False, category="validation"
            ).model_dump_json()
        result = audit(
            transcript=transcript,
            program=program,
            graph=graph,
            catalog=catalog,
            current_term=current_term,
            current_year=current_year,
        )
        remaining_reqs = [r.requirement_id for r in result.remaining_requirements]
        remaining_credits = int(result.remaining_credits)
        eligible = list(result.eligible_now)

        from langchain_core.messages import HumanMessage, SystemMessage

        from keel.services.prompts import c2_audit_summary_prompt

        prompt = c2_audit_summary_prompt.build(
            remaining_requirements=remaining_reqs,
            remaining_credits=remaining_credits,
            eligible_courses=eligible,
        )
        res = await llm.ainvoke([SystemMessage(content=prompt), HumanMessage(content="Summarize.")])
        narrative = _extract_advise_text(res.content)
        return (
            f"**Degree audit**\n"
            f"- Remaining requirement groups: {', '.join(remaining_reqs) or 'none'}\n"
            f"- Remaining credits: {remaining_credits}\n"
            f"- Eligible now: {', '.join(eligible) or 'none'}\n\n{narrative}"
        )
    except Exception as exc:
        _log.error("tool.degree_audit_chat.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def failure_recovery(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    llm: Any,
    student_id: str,
    tenant_id: str,
    failed_course: Any,
) -> str:
    """Extracted from the failure_recovery advising tool (behaviour unchanged)."""
    try:
        failed_course = failed_course.upper()
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            return ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            ).model_dump_json()
        transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, year)
        if program is None:
            return ToolError(
                error="Student has no program.", retryable=False, category="validation"
            ).model_dump_json()

        # Bake the failure into the transcript: the course is now NOT passed.
        failure_transcript = [t for t in transcript if t.course_code != failed_course]
        from decimal import Decimal as _Dec

        failure_transcript.append(
            TranscriptEntry(
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                course_code=failed_course,
                grade=_Dec("0.0"),
                passed=False,
                term=term,
                year=year - 1,
            )
        )
        recovery_audit = audit(
            transcript=failure_transcript,
            program=program,
            graph=graph,
            catalog=catalog,
            current_term=term,
            current_year=year,
        )

        # Downstream impact: courses that require the failed course (delayed).
        delayed = [
            r["course_code"]
            for r in data.get("prereq_rows", [])
            if r["requires_code"] == failed_course
        ]
        remaining_credits = int(recovery_audit.remaining_credits)
        extra_terms = max(1, -(-remaining_credits // 15))
        new_grad = _term_after(term, year, extra_terms)

        # Reuse the propose→verify→repair loop (lazy import avoids a cycle).
        from keel.agent.llm.plan_llm import _validate_with_repair
        from keel.domain.engine.planner import greedy_plan
        from keel.domain.engine.verifier import verify
        from keel.domain.engine.workload import compute_workload

        seed_codes = ([failed_course] if failed_course in catalog else []) + [
            c for c in recovery_audit.eligible_now if c != failed_course
        ][:4]
        plan = await _validate_with_repair(
            proposed={"courses": seed_codes},
            catalog=catalog,
            graph=graph,
            transcript=failure_transcript,
            coreqs=coreqs,
            term=term,
            year=year,
            student_id=student_id,
            tenant_id=tenant_id,
            program=program,
            llm=llm,
        )
        if plan is None:
            plan = greedy_plan(
                transcript=failure_transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                corequisites=coreqs,
                start_term=term,
                start_year=year,
                student_id_hint=student_id,
            )
        if plan is None or not plan.terms:
            delayed_txt = ", ".join(delayed) or "no downstream courses"
            return (
                f"Failing {failed_course} delays {delayed_txt}. "
                "I couldn't auto-build a recovery plan — please consult your advisor."
            )

        # Confirm verifier-valid (the type-level guarantee of a recovery plan).
        first_term = plan.terms[0]
        violations = verify(
            plan=plan,
            catalog=catalog,
            graph=graph,
            transcript=failure_transcript,
            corequisites=coreqs,
            current_term=term,
            current_year=year,
        )
        valid = not violations
        courses = [catalog[c] for c in first_term.course_codes if c in catalog]
        _, band = compute_workload(courses)
        course_lines = ", ".join(first_term.course_codes)
        check = "engine-verified ✓" if valid else "⚠ needs advisor review"
        next_label = f"{first_term.term.value.title()} {first_term.year}, {band.value} load"
        return (
            f"**Recovery plan after failing {failed_course}** ({check})\n"
            f"- Downstream delayed: {', '.join(delayed) or 'none'}\n"
            f"- Revised graduation estimate: {new_grad} (~{extra_terms} extra term(s))\n"
            f"- Next term ({next_label}): {course_lines}\n\n"
            "Retake the failed course as soon as it's offered. This is a plan, not a "
            "registration — say the word and I'll help you enroll."
        )
    except Exception as exc:
        _log.error("tool.failure_recovery.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def major_switch_advice(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    llm: Any,
    student_id: str,
    tenant_id: str,
    target_program: Any,
) -> str:
    """Extracted from the major_switch_advice advising tool (behaviour unchanged)."""
    try:
        target_program = target_program.upper()
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()
            transcript, catalog, graph, coreqs, own_program = _build_engine_objects(
                data, term, year
            )
            target = await _load_program_engine(_db, tenant_id, target_program, catalog)
        if target is None:
            return ToolError(
                error=f"Unknown target program '{target_program}'.",
                retryable=False,
                category="validation",
            ).model_dump_json()

        target_audit = audit(
            transcript=transcript,
            program=target,
            graph=graph,
            catalog=catalog,
            current_term=term,
            current_year=year,
        )
        impact = _compute_switch_impact(
            transcript=transcript,
            catalog=catalog,
            target_program=target,
            target_audit=target_audit,
            current_term=term,
            current_year=year,
        )

        from langchain_core.messages import HumanMessage, SystemMessage

        from keel.services.prompts import c4_major_switch_prompt

        prompt = c4_major_switch_prompt.build(
            target_program=target_program,
            lost_credits=impact["lost_credits"],
            new_graduation_term=impact["new_graduation_term"],
            delayed_courses=impact["delayed_courses"],
        )
        res = await llm.ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content="Give your recommendation.")]
        )
        recommendation = _extract_advise_text(res.content)
        return (
            f"**Switching to {target_program} — consequences (engine-computed)**\n"
            f"- Credits that wouldn't count: {impact['lost_credits']}\n"
            f"- New graduation estimate: {impact['new_graduation_term']} "
            f"(~{impact['extra_terms']} extra term(s))\n"
            f"- Courses still gated by prerequisites: "
            f"{', '.join(impact['delayed_courses']) or 'none'}\n\n"
            f"{recommendation}\n\n_Advisory only — not a guarantee._"
        )
    except Exception as exc:
        _log.error("tool.major_switch_advice.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()
