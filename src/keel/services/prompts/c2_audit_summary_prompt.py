"""C2 — Degree Audit Chat narrative (spec Appendix C).

The LLM restates engine numbers verbatim. It never recomputes, rounds, or invents.
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """You explain a student's degree audit in plain language.
You are given exact numbers from the engine. Restate them EXACTLY.
Never recompute, round, or invent any number.

AUDIT (engine, authoritative):
  remaining_requirements: {remaining_requirements}
  remaining_credits: {remaining_credits}
  eligible_courses: {eligible_courses}

Write 2-4 short sentences. Use the numbers above verbatim."""


def build(
    *,
    remaining_requirements: list[str],
    remaining_credits: int,
    eligible_courses: list[str],
) -> str:
    return _TEMPLATE.format(
        remaining_requirements=", ".join(remaining_requirements) or "none",
        remaining_credits=remaining_credits,
        eligible_courses=", ".join(eligible_courses) or "none",
    )
