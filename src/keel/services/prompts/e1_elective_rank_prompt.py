"""E1 — Elective ranking (spec Appendix C). Ranks ONLY the engine-eligible electives."""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """Rank ONLY the eligible electives below. You may not add any course
not in this list. Justify each by the student's strengths and preferences.

ELIGIBLE ELECTIVES (engine): {eligible_electives}
STUDENT STRENGTHS: {strengths}
PREFERENCES: {prefs}

Return a ranked list, one course per line, each with a one-line reason.
Use ONLY the course codes from the eligible list."""


def build(
    *,
    eligible_electives: list[str],
    strengths: str,
    prefs: str,
) -> str:
    return _TEMPLATE.format(
        eligible_electives=", ".join(eligible_electives) or "none",
        strengths=strengths,
        prefs=prefs,
    )
