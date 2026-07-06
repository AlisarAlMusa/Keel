"""Graduation-planning use cases: build the full term-by-term path to graduation
and manage the student's single active saved plan (load / delete / swap).

Extracted from the planning agent tools; behaviour unchanged.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.agent.llm.plan_llm import (
    _llm_propose_grad_paths,
    _validate_grad_path,
)
from keel.agent.plan_channel import emit_plans
from keel.domain.engine.audit import audit
from keel.domain.engine.contracts import Plan
from keel.domain.engine.planner import greedy_plan
from keel.domain.models import Course, Term
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.mappers.engine_context import _build_engine_objects
from keel.presenters.grad_card import build_grad_plan_card
from keel.repositories.students import StudentRepository
from keel.services.grad_plans import (
    delete_active_grad_plan,
    load_active_grad_plan,
    swap_active_grad_plan_course,
)

_log = get_logger(__name__)


async def plan_graduation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    llm: Any,
    model_client: Any,
    student_id: str,
    tenant_id: str,
    start_term: Any,
    start_year: Any,
    prefer_courses: Any,
) -> str:
    """Extracted from the plan_graduation tool (behaviour unchanged)."""
    try:
        term = Term(start_term.lower())
    except ValueError:
        return ToolError(
            error=f"Invalid term '{start_term}'. Use: fall, spring, summer.",
            retryable=False,
            category="validation",
        ).model_dump_json()
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            return ToolError(
                error=f"Student {student_id} not found.",
                retryable=False,
                category="validation",
            ).model_dump_json()
        if data["student"].get("has_hold"):
            reason = data["student"].get("hold_reason") or "unknown"
            return ToolError(
                error=f"Student has an active hold ({reason}). Resolve it before planning.",
                retryable=False,
                category="validation",
            ).model_dump_json()

        # Anchor the path to the student's ACTUAL current term/year (authoritative,
        # from the record) — never a past term, regardless of what the LLM passed.
        stu = data["student"]
        if stu.get("current_term"):
            try:
                term = Term(str(stu["current_term"]).lower())
            except ValueError:
                pass
        if stu.get("current_year"):
            start_year = int(stu["current_year"])

        transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, start_year)
        if program is None:
            return ToolError(
                error="Student has no program — cannot plan a path.",
                retryable=False,
                category="validation",
            ).model_dump_json()

        # Graduation paths: the LLM PROPOSES the pacing, the engine VERIFIES (mirrors
        # the planning loop). The engine first computes WHICH courses must be taken (the
        # greedy closure incl. prereqs); the LLM arranges them into 2–3 goal-paced
        # multi-term paths; each is engine-validated. Greedy is the FALLBACK only.
        blurbs = {
            "Fastest": "Heaviest terms, finish soonest",
            "Balanced": "A steady, manageable load",
            "Lighter": "Fewer courses per term",
        }
        # Career-aligned electives to prioritise (capped + catalog-checked, so a bad hint
        # can't distort the plan — the engine still only schedules valid courses).
        prefer_codes = frozenset(c.upper() for c in (prefer_courses or []) if c.upper() in catalog)
        base = greedy_plan(
            transcript=transcript,
            program=program,
            graph=graph,
            catalog=catalog,
            corequisites=coreqs,
            start_term=term,
            start_year=start_year,
            student_id_hint=student_id,
            prefer_codes=prefer_codes,
        )
        if base is None or not base.terms:
            return (
                "I couldn't compute a path to graduation — there may be prerequisite gaps "
                "or requirements I can't schedule. Please consult your advisor."
            )
        required = {c for pt in base.terms for c in pt.course_codes}

        cards: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()

        def _add_card(plan: Plan, label: str) -> None:
            sig = tuple(
                (pt.term.value, pt.year, tuple(sorted(pt.course_codes))) for pt in plan.terms
            )
            if sig in seen:
                return
            seen.add(sig)
            cards.append(
                _build_grad_card(
                    plan,
                    catalog,
                    program=program,
                    card_id=f"grad-{len(cards) + 1}",
                    label=label,
                    blurb=blurbs.get(label, "Path to graduation"),
                )
            )

        # LLM proposes pacing over the required set → engine validates each path.
        proposed = await _llm_propose_grad_paths(
            llm=llm,
            courses=sorted(required),
            catalog=catalog,
            graph=graph,
            start_term=term,
            start_year=start_year,
        )
        for cand in proposed:
            valid = _validate_grad_path(
                path=cand,
                required=required,
                catalog=catalog,
                graph=graph,
                transcript=transcript,
                student_id=student_id,
                tenant_id=tenant_id,
                program=program,
            )
            if valid is not None:
                _add_card(valid, str(cand.get("label") or f"Option {len(cards) + 1}"))

        # Ensure the student gets at least 2 options: if the LLM yielded fewer engine-valid
        # paths, top up with greedy variants (each valid by construction; greedy is the
        # safety net, the LLM stays the primary proposer).
        if len(cards) < 2:
            _log.info("tool.plan_graduation.greedy_topup", llm_valid=len(cards))
            used = {c["label"] for c in cards}
            for label, cap in (("Balanced", 15), ("Fastest", 18), ("Lighter", 12)):
                if len(cards) >= 3 or label in used:
                    continue
                p = (
                    base
                    if cap == 15
                    else greedy_plan(
                        transcript=transcript,
                        program=program,
                        graph=graph,
                        catalog=catalog,
                        corequisites=coreqs,
                        start_term=term,
                        start_year=start_year,
                        credit_cap=cap,
                        student_id_hint=student_id,
                        prefer_codes=prefer_codes,
                    )
                )
                if p is not None and p.terms:
                    _add_card(p, label)

        if not cards:
            return (
                "I couldn't compute a complete path to graduation automatically — there "
                "may be prerequisite gaps or remaining requirements I can't schedule "
                "within a reasonable horizon. Please consult your advisor."
            )

        # Near-term graduation risk (immediate term — same across variants).
        risk_note = ""
        if model_client is not None:
            try:
                from keel.domain.engine.risk_inputs import score_plan_term

                audit_result = audit(
                    transcript=transcript,
                    program=program,
                    graph=graph,
                    catalog=catalog,
                    current_term=term,
                    current_year=start_year,
                )
                # Use the first variant's first term as the immediate-term proxy.
                first_codes = [
                    str(c.get("code"))
                    for c in cards[0]["terms"][0]["courses"]
                    if isinstance(c, dict) and c.get("code")
                ]
                from keel.domain.engine.contracts import PlanTerm as _PT

                pt0 = _PT(term=term, year=start_year, course_codes=first_codes)
                _, vector = score_plan_term(audit_result, pt0, catalog)
                pred = await model_client.predict_grad_risk(vector.tolist())
                if pred:
                    risk_note = f" Near-term graduation risk: {pred.label} ({pred.score:.0%})."
            except Exception as exc:  # noqa: BLE001
                _log.warning("tool.plan_graduation.risk_failed", error=str(exc))

        # Surface the structured grad-plan cards to the widget.
        emit_plans(cards)

        opt_lines = [
            f"- **{c['label']}** — {c['termsToGrad']} terms, graduate {c['graduates']} "
            f"(heaviest: {c['heaviestTerm']})"
            for c in cards
        ]
        summary = (
            f"Here {'is' if len(cards) == 1 else 'are'} {len(cards)} path"
            f"{'' if len(cards) == 1 else 's'} to graduation — pick the one that fits:\n"
            + "\n".join(opt_lines)
            + risk_note
        )

        # Honesty check for a career-goal request: report which preferred courses actually
        # made it into the plan, and which could NOT (they aren't part of this program's
        # requirements). Prevents claiming an out-of-program course is "in the plan".
        if prefer_codes:
            planned = {
                str(c.get("code"))
                for card in cards
                for t in card["terms"]
                for c in t["courses"]
                if isinstance(c, dict)
            }
            got = sorted(prefer_codes & planned)
            missed = sorted(prefer_codes - planned)
            if got:
                summary += f"\n\nPrioritised toward your goal: {', '.join(got)}."
            if missed:
                summary += (
                    f" Note: {', '.join(missed)} {'is' if len(missed) == 1 else 'are'} not "
                    "part of your current program's requirements, so "
                    f"{'it' if len(missed) == 1 else 'they'} could not be added — taking "
                    f"{'it' if len(missed) == 1 else 'them'} would require a major change. "
                    "Tell the student this honestly; do not claim a course is in the plan "
                    "when it is not."
                )

        _log.info(
            "tool.plan_graduation.done",
            student_id=student_id,
            variants=len(cards),
            tenant_id=tenant_id,
        )
        return summary

    except Exception as exc:
        _log.error("tool.plan_graduation.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def load_grad_plan(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
) -> str:
    """Extracted from the load_grad_plan tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            result = await load_active_grad_plan(
                session,
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
            )
        if result is None:
            return "You do not have a saved graduation plan yet."
        if result.card:
            emit_plans([result.card])
        return result.message
    except Exception as exc:
        _log.error("tool.load_grad_plan.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def delete_grad_plan(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
) -> str:
    """Extracted from the delete_grad_plan tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            result = await delete_active_grad_plan(
                session,
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
            )
        return result.message
    except Exception as exc:
        _log.error("tool.delete_grad_plan.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def swap_grad_plan_course(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
    remove_code: Any,
    add_code: Any,
) -> str:
    """Extracted from the swap_grad_plan_course tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            result = await swap_active_grad_plan_course(
                session,
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                remove_code=remove_code,
                add_code=add_code,
            )
        if result.card:
            emit_plans([result.card])
        return result.message
    except ValueError as exc:
        return ToolError(error=str(exc), retryable=False, category="validation").model_dump_json()
    except Exception as exc:
        _log.error("tool.swap_grad_plan_course.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


def _build_grad_card(
    plan: Plan,
    catalog: dict[str, Course],
    *,
    program: Any | None = None,
    card_id: str,
    label: str,
    blurb: str,
) -> dict[str, Any]:
    """Convert a verifier-valid multi-term Plan into a widget grad-plan card."""
    return build_grad_plan_card(
        plan,
        catalog,
        program,
        card_id=card_id,
        label=label,
        blurb=blurb,
    )
