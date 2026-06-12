"""Graduation-risk feature engineering (spec §4).

Single source of truth for the 9-feature contract. Both the offline data
generator and the online predict_risk tool call compute_features — no second
copy is allowed.

Never throws on bad or edge-case inputs: divide-by-zero guards use
max(denom, 1); all outputs are clamped to their documented ranges.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ── Fixed constants (frozen — changing any requires a DECISIONS.md entry) ──

EXPECTED_CREDITS_PER_TERM: int = 15
TREND_WINDOW: int = 2
HARD_DIFFICULTY_THRESHOLD: int = 4  # difficulty scale is 1..5

# ── Feature order (frozen — model input, ONNX input, and feature_schema.json
#    all use this order; never reorder without retraining) ──────────────────

FEATURE_ORDER: list[str] = [
    "cumulative_gpa",
    "gpa_trend",
    "num_failures",
    "num_repeats",
    "progress_rate",
    "pct_complete",
    "planned_credits",
    "planned_workload_index",
    "num_hard_courses",
]


@dataclass(frozen=True)
class RawFeatureInputs:
    """Raw inputs needed to compute the 9 features.

    Attributes
    ----------
    cumulative_gpa:
        Credit-weighted GPA over the full transcript.
    recent_term_gpas:
        Per-term GPAs in chronological order (oldest → newest).
        The last TREND_WINDOW values are used for the trend.
    num_failures:
        Count of F or W grades in the transcript.
    num_repeats:
        Count of courses taken more than once.
    completed_credits:
        Total credits the student has passed.
    required_credits:
        Total credits required to graduate.
    terms_elapsed:
        Number of terms the student has been enrolled (≥ 1).
    plan_courses:
        List of (credits, difficulty) tuples for every course in the
        candidate plan. difficulty is on the 1..5 scale.
    """

    cumulative_gpa: float
    recent_term_gpas: list[float]
    num_failures: int
    num_repeats: int
    completed_credits: float
    required_credits: float
    terms_elapsed: int
    plan_courses: list[tuple[int, int]]


def compute_features(raw: RawFeatureInputs) -> dict[str, float]:
    """Turn raw inputs into the 9-feature dict (keys == FEATURE_ORDER).

    Pure and deterministic. Never raises; edge cases are clamped or guarded.
    """
    # ── gpa_trend: mean of last TREND_WINDOW term GPAs minus cumulative ───
    window = raw.recent_term_gpas[-TREND_WINDOW:] if raw.recent_term_gpas else []
    if window:
        gpa_trend = float(np.mean(window)) - raw.cumulative_gpa
    else:
        gpa_trend = 0.0

    # ── progress_rate: completed / (elapsed × expected) ───────────────────
    denom_progress = max(raw.terms_elapsed, 1) * EXPECTED_CREDITS_PER_TERM
    progress_rate = raw.completed_credits / denom_progress

    # ── pct_complete: completed / required, capped at 1 ───────────────────
    denom_required = max(raw.required_credits, 1)
    pct_complete = min(raw.completed_credits / denom_required, 1.0)

    # ── plan features ──────────────────────────────────────────────────────
    planned_credits = sum(c for c, _ in raw.plan_courses)
    planned_workload_index = float(
        sum(c * d for c, d in raw.plan_courses)
    )
    num_hard_courses = sum(
        1 for _, d in raw.plan_courses if d >= HARD_DIFFICULTY_THRESHOLD
    )

    return {
        "cumulative_gpa": float(raw.cumulative_gpa),
        "gpa_trend": float(gpa_trend),
        "num_failures": float(raw.num_failures),
        "num_repeats": float(raw.num_repeats),
        "progress_rate": float(progress_rate),
        "pct_complete": float(pct_complete),
        "planned_credits": float(planned_credits),
        "planned_workload_index": planned_workload_index,
        "num_hard_courses": float(num_hard_courses),
    }


def to_vector(features: dict[str, float]) -> np.ndarray:
    """Convert a feature dict to a 1-D numpy array in FEATURE_ORDER.

    The order must match the model's expected input order exactly.
    """
    return np.array([features[name] for name in FEATURE_ORDER], dtype=np.float64)
