"""E2 — Career-path mapping (spec Appendix C). Grounded suggestion, not a prediction."""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """Map the student's interest "{interest}" to a direction, relevant
skills, and catalog electives. You may ONLY name courses from the catalog list.
This is a grounded SUGGESTION, not a prediction — say so.

CATALOG ELECTIVES (grounding): {catalog_electives}

Output four labelled sections:
  DIRECTION: one or two sentences.
  COURSES: only codes from the list above.
  SKILLS: a short comma-separated list.
  CAVEAT: state explicitly this is a grounded suggestion, not a prediction."""


def build(*, interest: str, catalog_electives: list[str]) -> str:
    return _TEMPLATE.format(
        interest=interest,
        catalog_electives=", ".join(catalog_electives) or "none",
    )
