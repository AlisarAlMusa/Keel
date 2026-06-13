"""Unit tests for domain/features/grad_risk.py.

All expected values are hand-computed so the tests catch regressions in the
feature formulas without relying on the model or any external data.
"""

import numpy as np
import pytest

from keel.domain.features.grad_risk import (
    EXPECTED_CREDITS_PER_TERM,
    FEATURE_ORDER,
    HARD_DIFFICULTY_THRESHOLD,
    TREND_WINDOW,
    RawFeatureInputs,
    compute_features,
    to_vector,
)


def _raw(**overrides):  # type: ignore[no-untyped-def]
    defaults = dict(
        cumulative_gpa=3.0,
        recent_term_gpas=[3.2, 2.8],
        num_failures=0,
        num_repeats=0,
        completed_credits=45.0,
        required_credits=120.0,
        terms_elapsed=3,
        plan_courses=[(3, 3), (4, 4), (3, 2)],
    )
    defaults.update(overrides)
    return RawFeatureInputs(**defaults)


# ── gpa_trend ──────────────────────────────────────────────────────────────


def test_gpa_trend_falling():
    raw = _raw(cumulative_gpa=3.5, recent_term_gpas=[3.0, 2.8])
    feats = compute_features(raw)
    # mean([3.0, 2.8]) - 3.5 = 2.9 - 3.5 = -0.6
    assert feats["gpa_trend"] == pytest.approx(-0.6, abs=1e-9)


def test_gpa_trend_rising():
    raw = _raw(cumulative_gpa=2.5, recent_term_gpas=[2.8, 3.2])
    feats = compute_features(raw)
    # mean([2.8, 3.2]) - 2.5 = 3.0 - 2.5 = 0.5
    assert feats["gpa_trend"] == pytest.approx(0.5, abs=1e-9)


def test_gpa_trend_empty_terms():
    # No recent terms → trend defaults to 0
    raw = _raw(recent_term_gpas=[])
    feats = compute_features(raw)
    assert feats["gpa_trend"] == pytest.approx(0.0)


def test_gpa_trend_uses_only_trend_window():
    # More terms than TREND_WINDOW — only last TREND_WINDOW used
    raw = _raw(cumulative_gpa=3.0, recent_term_gpas=[4.0, 4.0, 2.0, 2.0])
    feats = compute_features(raw)
    # mean([2.0, 2.0]) - 3.0 = 2.0 - 3.0 = -1.0
    assert feats["gpa_trend"] == pytest.approx(-1.0, abs=1e-9)


# ── progress_rate ──────────────────────────────────────────────────────────


def test_progress_rate_normal():
    # completed=45, terms=3, expected=15 → 45/(3*15)=1.0
    raw = _raw(completed_credits=45.0, terms_elapsed=3)
    feats = compute_features(raw)
    assert feats["progress_rate"] == pytest.approx(1.0)


def test_progress_rate_behind():
    # completed=30, terms=3 → 30/45 ≈ 0.667
    raw = _raw(completed_credits=30.0, terms_elapsed=3)
    feats = compute_features(raw)
    assert feats["progress_rate"] == pytest.approx(30.0 / 45.0, abs=1e-9)


def test_progress_rate_zero_terms_no_crash():
    # terms_elapsed=0 → guard clamps denom to 1
    raw = _raw(completed_credits=0.0, terms_elapsed=0)
    feats = compute_features(raw)
    assert feats["progress_rate"] == pytest.approx(0.0)


# ── pct_complete ───────────────────────────────────────────────────────────


def test_pct_complete_partial():
    raw = _raw(completed_credits=60.0, required_credits=120.0)
    feats = compute_features(raw)
    assert feats["pct_complete"] == pytest.approx(0.5)


def test_pct_complete_capped_at_one():
    # More credits than required → capped at 1.0
    raw = _raw(completed_credits=130.0, required_credits=120.0)
    feats = compute_features(raw)
    assert feats["pct_complete"] == pytest.approx(1.0)


def test_pct_complete_zero_required_no_crash():
    # required_credits=0 → guard clamps denom to 1
    raw = _raw(completed_credits=0.0, required_credits=0.0)
    feats = compute_features(raw)
    assert feats["pct_complete"] == pytest.approx(0.0)


# ── plan features ──────────────────────────────────────────────────────────


def test_planned_credits_sum():
    raw = _raw(plan_courses=[(3, 2), (4, 5), (3, 1)])
    feats = compute_features(raw)
    assert feats["planned_credits"] == pytest.approx(10.0)


def test_planned_workload_index():
    # 3*2 + 4*5 + 3*1 = 6 + 20 + 3 = 29
    raw = _raw(plan_courses=[(3, 2), (4, 5), (3, 1)])
    feats = compute_features(raw)
    assert feats["planned_workload_index"] == pytest.approx(29.0)


def test_num_hard_courses():
    # difficulty >= HARD_DIFFICULTY_THRESHOLD(=4): courses with diff 4 and 5
    raw = _raw(plan_courses=[(3, 3), (4, 4), (3, 5), (3, 2)])
    feats = compute_features(raw)
    assert feats["num_hard_courses"] == pytest.approx(2.0)


def test_empty_plan_courses():
    raw = _raw(plan_courses=[])
    feats = compute_features(raw)
    assert feats["planned_credits"] == pytest.approx(0.0)
    assert feats["planned_workload_index"] == pytest.approx(0.0)
    assert feats["num_hard_courses"] == pytest.approx(0.0)


# ── to_vector ──────────────────────────────────────────────────────────────


def test_to_vector_order_and_length():
    raw = _raw()
    feats = compute_features(raw)
    vec = to_vector(feats)
    assert vec.shape == (len(FEATURE_ORDER),)
    for i, name in enumerate(FEATURE_ORDER):
        assert vec[i] == pytest.approx(feats[name])


def test_to_vector_dtype():
    vec = to_vector(compute_features(_raw()))
    assert vec.dtype == np.float64


# ── FEATURE_ORDER completeness ─────────────────────────────────────────────


def test_feature_order_length():
    assert len(FEATURE_ORDER) == 9


def test_compute_features_returns_all_keys():
    feats = compute_features(_raw())
    assert set(feats.keys()) == set(FEATURE_ORDER)


# ── Constants sanity ───────────────────────────────────────────────────────


def test_constants():
    assert EXPECTED_CREDITS_PER_TERM == 15
    assert TREND_WINDOW == 2
    assert HARD_DIFFICULTY_THRESHOLD == 4
