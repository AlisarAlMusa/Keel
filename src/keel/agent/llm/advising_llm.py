"""LLM orchestration for advising (risk-mitigation narration + LLM text extraction).

Moved verbatim from ``agent/tools/advising.py`` - prompt wording and control flow
are byte-identical. The engine/model produce the deterministic risk label and
reasons; ``_llm_mitigation`` only narrates an actionable plan from them.
"""

from __future__ import annotations

from typing import Any


def _extract_advise_text(content: Any) -> str:
    """Extract plain text from LLM content that may be a list of blocks (Gemini)."""
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
            if not isinstance(block, dict) or block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


async def _llm_mitigation(
    *,
    llm: Any,
    label: str,
    score: float,
    reasons: str,
    course_codes: list[str],
) -> str:
    """Ask the LLM to write a mitigation plan given deterministic reasons.

    The LLM explains and suggests — it does NOT compute features or decide the label.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    if label == "on_track":
        return "Your plan looks healthy. Keep attending office hours and stay on schedule."

    prompt = (
        f"A student's graduation-risk model returned: {label.upper()} ({score:.0%} confidence).\n"
        f"Key risk factors (deterministic, not your analysis):\n{reasons}\n"
        f"Proposed courses: {', '.join(course_codes)}\n\n"
        "Write a concise (3-5 bullet points) mitigation plan to help the student reduce risk. "
        "Focus on actionable steps. Do not re-state the risk factors verbatim."
    )
    try:
        result = await llm.ainvoke(
            [
                SystemMessage(content="You are a supportive academic advisor."),
                HumanMessage(content=prompt),
            ]
        )
        return str(result.content)
    except Exception:
        return "Please speak with your academic advisor to discuss a mitigation strategy."
