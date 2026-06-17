"""F4 — Advisor handoff summary (spec Appendix C). Factual and brief."""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """Write an advisor handoff summary for an escalation.
Include: conversation recap, transcript summary, failed constraints,
recommended actions. Factual and brief.

CONVERSATION: {recap}
TRANSCRIPT: {transcript_summary}
FAILED CONSTRAINTS (engine): {failed_constraints}

Output a structured handoff the advisor can read in under a minute."""


def build(
    *,
    recap: str,
    transcript_summary: str,
    failed_constraints: str,
) -> str:
    return _TEMPLATE.format(
        recap=recap,
        transcript_summary=transcript_summary,
        failed_constraints=failed_constraints or "none",
    )
