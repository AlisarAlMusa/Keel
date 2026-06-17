"""F3 — Petition draft (spec Appendix C).

The engine has BLOCKED enrollment; the LLM drafts a REQUEST to a human reviewer,
never claiming the block is lifted.
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """Draft a prerequisite-override PETITION for a human reviewer.
The engine has BLOCKED enrollment; you are drafting a REQUEST, not enrolling.
Base the justification on the student's stated reason + transcript context.
Be honest and specific. Do not claim the block is lifted.

COURSE: {course_id}
ENGINE BLOCK REASON: {block_reason}
STUDENT JUSTIFICATION: {justification}
TRANSCRIPT CONTEXT: {transcript_summary}

Write a concise, respectful petition (one paragraph)."""


def build(
    *,
    course_id: str,
    block_reason: str,
    justification: str,
    transcript_summary: str,
) -> str:
    return _TEMPLATE.format(
        course_id=course_id,
        block_reason=block_reason,
        justification=justification,
        transcript_summary=transcript_summary,
    )
