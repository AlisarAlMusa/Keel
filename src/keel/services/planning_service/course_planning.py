"""Course-planning use cases: propose plans for the immediate term, build
conflict-free section schedules, and run what-if degree simulations.

Extracted from the planning agent tools; behaviour unchanged.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.agent.llm.plan_llm import (
    _extract_llm_text,
    _llm_propose_multi,
    _llm_rank,
    _make_plan,
    _validate_with_repair,
)
from keel.agent.plan_channel import emit_plans
from keel.domain.engine.audit import audit
from keel.domain.engine.contracts import Plan
from keel.domain.engine.planner import greedy_plan
from keel.domain.engine.sections import find_conflict_free_combinations
from keel.domain.engine.verifier import verify
from keel.domain.engine.workload import compute_workload
from keel.domain.models import Section, Term
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.mappers.engine_context import _build_engine_objects
from keel.mappers.sections import _row_to_section
from keel.presenters.grad_card import requirement_label
from keel.presenters.plan_cards import _build_plan_cards, _fmt_section_slots, _pref_summary
from keel.repositories.sections import SectionRepository
from keel.repositories.students import StudentRepository

_log = get_logger(__name__)


async def propose_plan(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    llm: Any,
    model_client: Any,
    student_id: str,
    tenant_id: str,
    start_term: str,
    start_year: int,
    excluded_days: list[str] | None,
    min_start_hour: int,
) -> str:
    """Build up to 3 feasible, risk-scored, workload-banded course plans (see tool docstring)."""
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
                error=f"Student has an active hold ({reason}). Resolve before planning.",
                retryable=False,
                category="validation",
            ).model_dump_json()

        # Anchor planning to the student's ACTUAL current term/year (authoritative,
        # from the record) so we never plan a past/empty term and the term is
        # consistent across students — same convention as plan_graduation.
        stu = data["student"]
        if stu.get("current_term"):
            try:
                term = Term(str(stu["current_term"]).lower())
            except ValueError:
                pass
        if stu.get("current_year"):
            start_year = int(stu["current_year"])
        # Keep the string form in sync — later section queries/cards use start_term.
        start_term = term.value

        transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, start_year)
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
            llm=llm,
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
                llm=llm,
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
            if model_client is not None:
                from keel.domain.engine.risk_inputs import score_plan_term

                _, vector = score_plan_term(audit_result, plan.terms[0], catalog)
                pred = await model_client.predict_grad_risk(vector.tolist())
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
            llm=llm,
            candidates=scored,
            catalog=catalog,
            audit_result=audit_result,
        )

        # --- Registrability check (always, regardless of preferences) ---
        # A plan can be academically valid yet contain a course with no OPEN section
        # in the target term (verify() checks prereqs/credits/offering, not live
        # seats). Flag those so the plan is honest about what can actually be
        # registered now — keep the course, don't silently drop it.
        if scored and scored[0]["plan"].terms:
            top_codes = list(scored[0]["plan"].terms[0].course_codes)
            if top_codes:
                try:
                    async with tenant_session(session_factory, UUID(tenant_id)) as _db:
                        seen_codes: dict[str, bool] = await SectionRepository(
                            _db, UUID(tenant_id)
                        ).open_status_for_courses(
                            term=start_term.lower(), year=start_year, codes=top_codes
                        )
                    # FULL = course has sections this term but none open → waitlist.
                    # NOT OFFERED = course has no section this term at all → another term.
                    full = [c for c in top_codes if c in seen_codes and not seen_codes[c]]
                    not_offered = [c for c in top_codes if c not in seen_codes]
                    notes: list[str] = []
                    if full:
                        notes.append(
                            f"all sections full for {', '.join(full)} — you can join the "
                            "waitlist for these"
                        )
                    if not_offered:
                        verb = "is" if len(not_offered) == 1 else "are"
                        notes.append(
                            f"{', '.join(not_offered)} {verb} not offered in "
                            f"{start_term.title()} {start_year} — plan these in a term "
                            "when they are offered (no waitlist applies)"
                        )
                    if notes:
                        ranking += "\n\n⚠ **Heads up:** " + "; ".join(notes) + "."
                except Exception as exc:  # noqa: BLE001
                    _log.warning("tool.propose_plan.registrability_check_failed", error=str(exc))

        # --- Section time preference check ---
        # Query sections for the first candidate's courses and report availability
        # filtered by the student's time preferences (excluded_days, min_start_hour).
        ex_days = {d.lower() for d in (excluded_days or [])}
        min_min = (min_start_hour or 0) * 60  # convert hour → minutes-since-midnight

        if scored and (ex_days or min_min > 0):
            first_codes = scored[0]["plan"].terms[0].course_codes if scored[0]["plan"].terms else []
            section_notes: list[str] = []
            if first_codes:
                try:
                    async with tenant_session(session_factory, UUID(tenant_id)) as _db:
                        open_rows = await SectionRepository(
                            _db, UUID(tenant_id)
                        ).open_section_slots_for_courses(
                            term=start_term.lower(),
                            year=start_year,
                            codes=list(first_codes),
                        )
                        sections_by_code: dict[str, list[list[dict[str, Any]]]] = {}
                        for r in open_rows:
                            sections_by_code.setdefault(r["course_code"], []).append(r["slots"])

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

                        def _fmt(slots: list[dict[str, Any]]) -> str:
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

        # G3: surface the structured candidates so the widget renders rich plan
        # cards (codes, credits, risk, workload) — not just the LLM's prose.
        emit_plans(
            _build_plan_cards(
                scored,
                catalog,
                start_term=start_term,
                start_year=start_year,
                program=program,
            )
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


async def propose_sections(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
    course_codes: Any,
    term: Any,
    year: Any,
    excluded_days: Any,
    min_start_hour: Any,
) -> str:
    """Extracted from the propose_sections tool (behaviour unchanged)."""
    if not course_codes:
        return ToolError(
            error="No course codes provided.", retryable=False, category="validation"
        ).model_dump_json()
    try:
        term_enum = Term(term.lower())
    except ValueError:
        return ToolError(
            error=f"Invalid term '{term}'. Use: fall, spring, summer.",
            retryable=False,
            category="validation",
        ).model_dump_json()

    ex_days = {d.lower() for d in (excluded_days or [])}
    min_min = (min_start_hour or 0) * 60
    has_prefs = bool(ex_days or min_min)
    tid = UUID(tenant_id)
    try:
        async with tenant_session(session_factory, tid) as session:
            section_rows = await SectionRepository(session, tid).list_for_courses(
                term=term.lower(), year=year, codes=list(course_codes)
            )
            by_code: dict[str, list[dict[str, Any]]] = {}
            for r in section_rows:
                by_code.setdefault(r["course_code"], []).append(r)
            data = await StudentRepository(session, tenant_id).load_context(student_id)

        if not data:
            return ToolError(
                error=f"Student {student_id} not found.",
                retryable=False,
                category="validation",
            ).model_dump_json()
        _transcript, catalog, _graph, _coreqs, program = _build_engine_objects(
            data, term_enum, year
        )

        # Partition each course into preference-fitting open sections vs unavailable
        # (with the WHY → so the agent offers the right remedy).
        fitting: dict[str, list[Section]] = {}
        meta: dict[str, dict[str, Any]] = {}  # section_id → {when, instructor, seats}
        unavailable: list[dict[str, str]] = []
        for code in course_codes:
            secs = by_code.get(code, [])
            open_secs = [s for s in secs if int(s["enrolled"]) < int(s["capacity"])]
            pref_secs = [
                s
                for s in open_secs
                if _section_meets_prefs(list(s["slots"] or []), ex_days, min_min)
            ]
            if pref_secs:
                objs: list[Section] = []
                for s in pref_secs:
                    objs.append(_row_to_section(s, term_enum, year, tid))
                    meta[str(s["id"])] = {
                        "when": _fmt_section_slots(list(s["slots"] or [])),
                        "instructor": s["instructor"] or "TBA",
                        "seats": int(s["capacity"]) - int(s["enrolled"]),
                    }
                fitting[code] = objs
                continue
            if not secs:
                unavailable.append(
                    {"code": code, "reason": "not offered this term", "remedy": "another term"}
                )
            elif not open_secs:
                unavailable.append(
                    {"code": code, "reason": "all sections full", "remedy": "waitlist"}
                )
            else:
                unavailable.append(
                    {
                        "code": code,
                        "reason": "no section matches your time preferences",
                        "remedy": "relax preferences",
                    }
                )

        # Engine builds complete, conflict-free combinations across the fitting sections.
        combos = find_conflict_free_combinations(fitting) if fitting else []
        # Fallback: if the fitting sections can't all coexist conflict-free, greedily
        # place as many as possible and report the ones that had to be dropped.
        if fitting and not combos:
            picked: dict[str, Section] = {}
            chosen: list[Section] = []
            for code in [c for c in course_codes if c in fitting]:
                placed = False
                for sec in fitting[code]:
                    if not any(_sections_conflict(sec, c) for c in chosen):
                        picked[code] = sec
                        chosen.append(sec)
                        placed = True
                        break
                if not placed:
                    unavailable.append(
                        {
                            "code": code,
                            "reason": "time conflict with the rest of your schedule",
                            "remedy": "drop or swap a course",
                        }
                    )
            if picked:
                combos = [picked]

        # Dedupe and cap at 3 schedule bundles.
        schedules: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for combo in combos:
            sig = tuple(sorted(str(sec.id) for sec in combo.values()))
            if sig in seen:
                continue
            seen.add(sig)
            items: list[dict[str, Any]] = []
            for code in course_codes:
                chosen_sec = combo.get(code)
                if chosen_sec is None:
                    continue
                m = meta[str(chosen_sec.id)]
                items.append(
                    {
                        "code": code,
                        "title": catalog[code].name if code in catalog else code,
                        "credits": int(catalog[code].credits) if code in catalog else 0,
                        "requirement": requirement_label(code, program),
                        "section_id": str(chosen_sec.id),
                        "when": m["when"],
                        "instructor": m["instructor"],
                        "seats": m["seats"],
                    }
                )
            schedules.append(
                {
                    "id": f"sched-{len(schedules) + 1}",
                    "label": f"Option {len(schedules) + 1}",
                    "items": items,
                }
            )
            if len(schedules) >= 3:
                break

        pref_summary = _pref_summary(ex_days, min_start_hour) if has_prefs else None

        emit_plans(
            [
                {
                    "kind": "sections",
                    "id": "sections-1",
                    "term": f"{term.title()} {year}",
                    "hasPrefs": has_prefs,
                    "prefSummary": pref_summary,
                    "schedules": schedules,
                    "unavailable": unavailable,
                }
            ]
        )

        # Compact text for the agent — it recommends ONE; the card shows them all.
        header = f"Section schedules for {term.title()} {year}"
        if pref_summary:
            header += f" (preferences: {pref_summary})"
        lines: list[str] = [header + ":"]
        if schedules:
            for sch in schedules:
                parts = [
                    f"{it['code']} {it['when']} · {it['instructor']} "
                    f"[section_id: {it['section_id']}]"
                    for it in sch["items"]
                ]
                lines.append(f"- {sch['label']}: " + "; ".join(parts))
            lines.append(
                "Recommend ONE option to the student. To enroll, call stage_enrollment "
                "with that option's section_ids (one per course)."
            )
        else:
            lines.append(
                "No conflict-free, preference-fitting schedule could be built for these courses."
            )
        if unavailable:
            lines.append(
                "Unavailable: "
                + "; ".join(
                    f"{u['code']} — {u['reason']} (remedy: {u['remedy']})" for u in unavailable
                )
            )

        _log.info(
            "tool.propose_sections.done",
            student_id=student_id,
            courses=len(course_codes),
            schedules=len(schedules),
            unavailable=len(unavailable),
            tenant_id=tenant_id,
        )
        return "\n".join(lines)

    except Exception as exc:
        _log.error("tool.propose_sections.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def simulate_whatif(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    llm: Any,
    student_id: str,
    tenant_id: str,
    start_term: Any,
    start_year: Any,
    hypothetical_courses: Any,
) -> str:
    """Extracted from the simulate_whatif tool (behaviour unchanged)."""
    try:
        term = Term(start_term.lower())
    except ValueError:
        return ToolError(
            error=f"Invalid term '{start_term}'.", retryable=False, category="validation"
        ).model_dump_json()

    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            return ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            ).model_dump_json()

        transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, start_year)
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
        result = await llm.ainvoke(
            [
                SystemMessage(content="You are an academic advisor."),
                HumanMessage(content=prompt),
            ]
        )
        return f"**What-if simulation (read-only):**\n{_extract_llm_text(result.content)}"

    except Exception as exc:
        _log.error("tool.simulate_whatif.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


def _section_meets_prefs(
    slots: list[dict[str, Any]], excluded_days: set[str], min_start_min: int
) -> bool:
    """True if a section violates no hard time preference (day / earliest-start)."""
    days = {str(s["day"]).lower() for s in slots}
    if days & excluded_days:
        return False
    earliest = min((int(s["start_min"]) for s in slots), default=0)
    return earliest >= min_start_min


def _sections_conflict(a: Section, b: Section) -> bool:
    """True if any meeting slot of section a overlaps any meeting slot of section b."""
    return any(ta.overlaps(tb) for ta in a.slots for tb in b.slots)
