"""LLM orchestration for course planning (propose -> engine-validate -> repair -> rank).

Moved verbatim from ``agent/tools/planning.py`` - prompts, constants, and control
flow are byte-identical. The engine owns feasibility; these functions only drive
the LLM's generative parts (propose candidates, pace a grad path, rank) and the
propose->verify->repair loop. The planning tools call these instead of defining
them inline (CLAUDE.md section 9: the agent proposes and explains; the engine verifies).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from keel.domain.engine.contracts import Plan, PlanMeta, PlanTerm
from keel.domain.engine.verifier import verify
from keel.domain.models import Course, Term, TranscriptEntry
from keel.logging import get_logger
from keel.presenters.plan_cards import _format_plan

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


async def _llm_propose_grad_paths(
    *,
    llm: Any,
    courses: list[str],
    catalog: dict[str, Course],
    graph: Any,
    start_term: Term,
    start_year: int,
) -> list[dict[str, Any]]:
    """LLM arranges the full remaining course set into 2–3 goal-paced multi-term paths.

    The engine supplies WHICH courses must be taken (the greedy closure); the LLM decides
    HOW to pace them across terms (the soft/generative part). Each path is engine-validated
    by the caller — this only proposes.
    """
    desc: list[str] = []
    for code in courses:
        c = catalog.get(code)
        if not c:
            continue
        prereqs = sorted(graph.direct_prereqs(code))
        offered = "/".join(sorted(t.value for t in c.offered_terms))
        desc.append(
            f"  {code}: {c.credits}cr, difficulty {c.difficulty}/5, offered {offered}, "
            f"prereqs: {', '.join(prereqs) or 'none'}"
        )
    total_credits = sum(catalog[c].credits for c in courses if c in catalog)
    prompt = (
        f"Arrange ALL of these remaining courses into a term-by-term path to graduation, "
        f"starting {start_term.value.title()} {start_year}:\n" + "\n".join(desc) + "\n\n"
        f"There are {total_credits} credits total to schedule.\n\n"
        "RULES (hard):\n"
        "- A course's prerequisites must be in an EARLIER term than the course.\n"
        "- Schedule a course only in a term it is offered (fall/spring).\n"
        "- Never exceed 18 credits in a term.\n"
        "- Terms run consecutively: Fall <Y> → Spring <Y+1> → Fall <Y+1> → …\n"
        "- Include EVERY listed course exactly once.\n\n"
        "LEVELING (very important for a good plan):\n"
        f"- Spread credits EVENLY across terms. Aim each term close to {total_credits} ÷ "
        "(number of terms) — do NOT front-load one heavy term and leave tiny tail terms.\n"
        "- Avoid terms with fewer than 9 credits unless it is the final term or prereq "
        "chains genuinely force it. A trailing 3-credit term after several full terms is a "
        "bad plan.\n"
        "- Use the FEWEST terms that keep the load even (prefer fuller, balanced terms over "
        "many half-empty ones).\n\n"
        f"Produce {_MAX_CANDIDATES} labelled variants that differ by pace (each still "
        "evenly leveled within itself):\n"
        "  'Fastest'  — ~16–18 credits/term, fewest semesters\n"
        "  'Balanced' — ~14 credits/term, steady\n"
        "  'Lighter'  — ~11–12 credits/term, more semesters\n\n"
        "Respond with ONLY a JSON array:\n"
        '[{"label":"Balanced","terms":[{"term":"fall","year":2026,"courses":["CS101"]},'
        '{"term":"spring","year":2027,"courses":["CS102"]}]}]'
    )
    try:
        result = await llm.ainvoke(
            [
                SystemMessage(
                    content=f"You are a graduation planner. prompt_version={_PROMPT_VERSION}"
                ),
                HumanMessage(content=prompt),
            ]
        )
        text = _extract_llm_text(result.content).strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        try:
            paths = json.loads(text)
        except json.JSONDecodeError:
            import ast

            paths = ast.literal_eval(text)
        if isinstance(paths, list):
            return paths[:_MAX_CANDIDATES]
    except Exception as exc:  # noqa: BLE001
        _log.warning("tool.plan_graduation.llm_propose_failed", error=str(exc))
    return []


def _validate_grad_path(
    *,
    path: dict[str, Any],
    required: set[str],
    catalog: dict[str, Course],
    graph: Any,
    transcript: list[TranscriptEntry],
    student_id: str,
    tenant_id: str,
    program: Any,
) -> Plan | None:
    """Engine-validate an LLM-proposed multi-term path. Returns a Plan or None.

    Cumulative checks per term (the engine's rules, not the LLM's word): every course is
    offered that term, its prerequisites are satisfied by earlier terms + transcript, the
    term is within the credit cap, and the path covers ALL required courses exactly.
    """
    passed: set[str] = {e.course_code for e in transcript if e.passed}
    acc = set(passed)
    scheduled: set[str] = set()
    plan_terms: list[PlanTerm] = []
    for t in path.get("terms", []):
        try:
            term_enum = Term(str(t.get("term", "")).lower())
            year = int(t.get("year"))
        except (ValueError, TypeError):
            return None
        # Drop any course the LLM listed that the student has ALREADY passed (re-planning a
        # passed course is a "repeat" the save-time verifier rejects), AND de-duplicate within
        # the term (the LLM sometimes lists the same course twice in one term — e.g. PHYS201L —
        # which must never reach the card or its save). dict.fromkeys preserves order.
        codes = list(
            dict.fromkeys(c for c in (t.get("courses") or []) if c in catalog and c not in passed)
        )
        if sum(catalog[c].credits for c in codes) > 18:
            return None
        for code in codes:
            if term_enum not in catalog[code].offered_terms:
                return None
            if not graph.prereqs_satisfied(code, frozenset(acc)):
                return None
            if code in scheduled:
                return None  # duplicate
        plan_terms.append(PlanTerm(term=term_enum, year=year, course_codes=codes))
        acc |= set(codes)
        scheduled |= set(codes)

    if not plan_terms or not required.issubset(scheduled):
        return None  # incomplete — doesn't cover every required course
    return Plan(
        plan_id=uuid4(),
        tenant_id=UUID(tenant_id),
        student_id=UUID(student_id),
        program_id=program.program_id,
        name="grad",
        version=1,
        active=False,
        terms=plan_terms,
        meta=PlanMeta(generated_by="llm", created_at=datetime.utcnow()),
    )
