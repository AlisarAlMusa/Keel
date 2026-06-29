"""F4 — Advisor handoff summary (spec Appendix C). Factual and brief."""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """Write an advisor handoff summary for an escalation.
Factual and brief.

Begin with EXACTLY this header, using the real values below verbatim — never
invent or leave bracketed placeholders like [Name] or [Date]:

  Advisor Handoff Summary: Escalation
  Student: {student_name}
  Student ID: {student_id}
  Date: {today}

Then include: conversation recap, transcript summary, failed constraints,
recommended actions.

CONVERSATION: {recap}
TRANSCRIPT: {transcript_summary}
FAILED CONSTRAINTS (engine): {failed_constraints}

Output a structured handoff the advisor can read in under a minute."""


def build(
    *,
    recap: str,
    transcript_summary: str,
    failed_constraints: str,
    student_name: str,
    student_id: str,
    today: str,
) -> str:
    return _TEMPLATE.format(
        recap=recap,
        transcript_summary=transcript_summary,
        failed_constraints=failed_constraints or "none",
        student_name=student_name,
        student_id=student_id,
        today=today,
    )
