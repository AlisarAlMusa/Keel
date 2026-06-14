"""Workload index — deterministic difficulty aggregation.

Computes sum(credits × difficulty) for a set of (credits, difficulty) pairs
and maps it to a light/medium/heavy band. This is the D2 workload subsystem.

The raw_index helper is also consumed by risk_inputs.py (feature 8 of the
graduation-risk model). Both must produce the same number for the same inputs —
that equality is asserted by the T8 guard test.

Source of truth: specs/004-phase-1-engine/spec.md §5; plan.md §3.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from keel.domain.models import Course

# Thresholds pinned here; changes require a DECISIONS.md entry.
_LIGHT_MAX: int = 36  # ≤ 36  → light   (e.g. 3 easy courses: 3×3×4=36)
_MEDIUM_MAX: int = 54  # ≤ 54  → medium  (e.g. 4 medium courses: 4×3×4.5≈54)
# > 54  → heavy


class WorkloadBand(StrEnum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


def raw_workload_index(courses: Sequence[tuple[int, int]]) -> float:
    """sum(credits × difficulty) for a sequence of (credits, difficulty) pairs.

    This is the canonical implementation. risk_inputs.py (feature 8) must call
    this function — not re-implement it.
    """
    return float(sum(c * d for c, d in courses))


def workload_band(index: float) -> WorkloadBand:
    """Map a raw workload index to a named band."""
    if index <= _LIGHT_MAX:
        return WorkloadBand.LIGHT
    if index <= _MEDIUM_MAX:
        return WorkloadBand.MEDIUM
    return WorkloadBand.HEAVY


def compute_workload(courses: Sequence[Course]) -> tuple[float, WorkloadBand]:
    """Compute raw index + band for a list of Course domain objects.

    Returns (raw_index, band).
    """
    pairs = [(c.credits, c.difficulty) for c in courses]
    index = raw_workload_index(pairs)
    return index, workload_band(index)
