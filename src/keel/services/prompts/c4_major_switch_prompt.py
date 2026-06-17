"""C4 — Major-switch recommendation (spec Appendix C). Advisory, not a guarantee."""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """Advise on switching to {target_program}. This is ADVISORY, not a guarantee.
Use only the engine-computed consequences below. Do not invent numbers.

CONSEQUENCES (engine):
  lost_credits: {lost_credits}
  new_graduation_term: {new_graduation_term}
  delayed_courses: {delayed_courses}

Give a balanced recommendation in 3-5 sentences. End with the advisory caveat."""


def build(
    *,
    target_program: str,
    lost_credits: int,
    new_graduation_term: str,
    delayed_courses: list[str],
) -> str:
    return _TEMPLATE.format(
        target_program=target_program,
        lost_credits=lost_credits,
        new_graduation_term=new_graduation_term,
        delayed_courses=", ".join(delayed_courses) or "none",
    )
