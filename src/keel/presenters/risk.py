"""Graduation-risk presentation: turn salient model features into human-readable
reasons. Deterministic (threshold-based) — the LLM later narrates a mitigation
plan from these reasons; it does not invent them.

Moved verbatim from ``agent/tools/advising.py`` — same output.
"""

from __future__ import annotations


def _build_risk_reasons(features: dict[str, float], label: str) -> str:
    """Derive human-readable risk reasons from salient feature values.

    Reasons are DETERMINISTIC — derived from feature thresholds, not LLM-invented.
    This is what the LLM uses to write the mitigation plan (it explains, not decides).
    """
    reasons: list[str] = []

    gpa = features.get("cumulative_gpa", 4.0)
    if gpa < 2.0:
        reasons.append(f"Cumulative GPA is low ({gpa:.2f}) — below the 2.0 threshold.")
    elif gpa < 2.5:
        reasons.append(f"Cumulative GPA is borderline ({gpa:.2f}).")

    gpa_trend = features.get("gpa_trend", 0.0)
    if gpa_trend < -0.3:
        reasons.append(f"GPA trend is declining ({gpa_trend:+.2f} per term).")

    failures = features.get("num_failures", 0)
    if failures > 0:
        reasons.append(f"{int(failures)} failed course(s) on transcript.")

    repeats = features.get("num_repeats", 0)
    if repeats > 0:
        reasons.append(f"{int(repeats)} repeated course(s) on transcript.")

    progress = features.get("progress_rate", 1.0)
    if progress < 0.8:
        reasons.append(f"Progress rate is below expected ({progress:.0%} of schedule).")

    workload = features.get("planned_workload_index", 0.0)
    if workload > 54:
        reasons.append(f"Planned workload is heavy (index {workload:.0f}).")

    hard = features.get("num_hard_courses", 0)
    if hard >= 2:
        reasons.append(f"{int(hard)} high-difficulty course(s) in the planned term.")

    if not reasons:
        reasons.append("No significant risk factors detected.")

    return "\n".join(f"• {r}" for r in reasons)
