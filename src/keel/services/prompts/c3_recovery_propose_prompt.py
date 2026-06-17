"""C3 — Failure-recovery proposal (feeds the propose-verify-repair loop, spec Appendix C).

The LLM proposes a recovery plan from the engine-supplied impact + eligible pool,
then repairs from verifier violations. Same generate→verify→repair loop as A1.
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_TEMPLATE = """A student failed {failed_course}. Propose a recovery plan.
You may ONLY use courses from the eligible pool below.
Respect term order. Do not exceed the per-term credit cap.
Output JSON matching the plan schema. No prose.

ENGINE FACTS:
  impact: {impact}
  eligible_pool: {eligible_courses}
  credit_cap_per_term: {credit_cap}

If the verifier returns violations, you will receive them and must
repair the plan. Fix exactly the violations; change nothing else.
VIOLATIONS (if any): {violations}"""


def build(
    *,
    failed_course: str,
    impact: str,
    eligible_courses: list[str],
    credit_cap: int,
    violations: str = "none",
) -> str:
    return _TEMPLATE.format(
        failed_course=failed_course,
        impact=impact,
        eligible_courses=", ".join(eligible_courses) or "none",
        credit_cap=credit_cap,
        violations=violations or "none",
    )
