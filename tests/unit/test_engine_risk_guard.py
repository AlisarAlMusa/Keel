"""T8 guard tests — the no-skew trip-wire (plan.md §6, tasks T8).

These tests assert that the engine consumes the shared grad_risk module
correctly and never re-implements its formulas or constants.

1. FEATURE_ORDER has exactly 9 features.
2. Engine imports constants from grad_risk.py (no local copy).
3. A hand-computed example matches compute_features output.
4. Feature 8 (planned_workload_index) == workload.raw_workload_index for
   the same courses — one helper, two consumers.
5. to_vector() follows FEATURE_ORDER exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
import pytest

from keel.domain.engine.contracts import AuditResult, PlanTerm
from keel.domain.engine.risk_inputs import build_risk_inputs, score_plan_term
from keel.domain.engine.workload import raw_workload_index
from keel.domain.features.grad_risk import (
    EXPECTED_CREDITS_PER_TERM,
    FEATURE_ORDER,
    HARD_DIFFICULTY_THRESHOLD,
    TREND_WINDOW,
    RawFeatureInputs,
    compute_features,
    to_vector,
)
from keel.domain.models import Course, Term

TENANT_ID = uuid4()


def _course(code: str, credits: int, difficulty: int) -> Course:
    return Course(
        tenant_id=TENANT_ID,
        code=code,
        name=code,
        credits=credits,
        difficulty=difficulty,
        offered_terms=frozenset([Term.FALL]),
    )


def _audit(**kw) -> AuditResult:
    defaults = dict(
        completed_requirements=[],
        remaining_requirements=[],
        credits_completed=30.0,
        total_credits_required=120.0,
        remaining_credits=90.0,
        pct_complete=0.25,
        progress_rate=1.0,
        terms_elapsed=2,
        eligible_now=[],
        cumulative_gpa=3.0,
        recent_term_gpas=[2.8, 3.2],
        num_failures=1,
        num_repeats=0,
    )
    defaults.update(kw)
    return AuditResult(**defaults)


class TestFeatureOrderGuard:
    def test_feature_order_has_exactly_9_features(self):
        assert len(FEATURE_ORDER) == 9

    def test_feature_order_names_match_spec(self):
        expected = [
            "cumulative_gpa", "gpa_trend", "num_failures", "num_repeats",
            "progress_rate", "pct_complete", "planned_credits",
            "planned_workload_index", "num_hard_courses",
        ]
        assert FEATURE_ORDER == expected

    def test_to_vector_follows_feature_order(self):
        raw = RawFeatureInputs(
            cumulative_gpa=3.0, recent_term_gpas=[2.8, 3.2],
            num_failures=1, num_repeats=0,
            completed_credits=30.0, required_credits=120.0,
            terms_elapsed=2, plan_courses=[(3, 4), (3, 5)],
        )
        features = compute_features(raw)
        vec = to_vector(features)
        assert vec.shape == (9,)
        for i, name in enumerate(FEATURE_ORDER):
            assert vec[i] == pytest.approx(features[name])


class TestConstantsNoLocalCopy:
    """Engine must import constants from grad_risk.py — no redefining them."""

    def test_expected_credits_per_term(self):
        # risk_inputs.py imports from grad_risk; this just confirms the value
        assert EXPECTED_CREDITS_PER_TERM == 15

    def test_trend_window(self):
        assert TREND_WINDOW == 2

    def test_hard_difficulty_threshold(self):
        assert HARD_DIFFICULTY_THRESHOLD == 4

    def test_engine_risk_inputs_uses_shared_module(self):
        # If risk_inputs.py re-implemented RawFeatureInputs it would be a
        # different class; check it's the same import.
        from keel.domain.engine import risk_inputs as ri
        from keel.domain.features import grad_risk as gr
        assert ri.RawFeatureInputs is gr.RawFeatureInputs
        assert ri.compute_features is gr.compute_features
        assert ri.to_vector is gr.to_vector
        assert ri.FEATURE_ORDER is gr.FEATURE_ORDER


class TestHandComputedExample:
    """Feature values must match hand-computed expectations."""

    def test_hand_computed_features(self):
        # Student: cumulative_gpa=3.0, last 2 terms=[2.8, 3.2]
        # gpa_trend = mean([2.8,3.2]) - 3.0 = 3.0 - 3.0 = 0.0
        # completed=30, required=120, terms=2
        # progress_rate = 30/(2*15) = 1.0
        # pct_complete = 30/120 = 0.25
        # plan: 2 courses: (3 cr, diff=4), (3 cr, diff=5)
        # planned_credits = 6
        # planned_workload_index = 3*4 + 3*5 = 12+15 = 27
        # num_hard_courses = 2 (both >= 4)
        raw = RawFeatureInputs(
            cumulative_gpa=3.0,
            recent_term_gpas=[2.8, 3.2],
            num_failures=1,
            num_repeats=0,
            completed_credits=30.0,
            required_credits=120.0,
            terms_elapsed=2,
            plan_courses=[(3, 4), (3, 5)],
        )
        f = compute_features(raw)

        assert f["cumulative_gpa"] == pytest.approx(3.0)
        assert f["gpa_trend"] == pytest.approx(0.0)
        assert f["num_failures"] == pytest.approx(1.0)
        assert f["num_repeats"] == pytest.approx(0.0)
        assert f["progress_rate"] == pytest.approx(1.0)
        assert f["pct_complete"] == pytest.approx(0.25)
        assert f["planned_credits"] == pytest.approx(6.0)
        assert f["planned_workload_index"] == pytest.approx(27.0)
        assert f["num_hard_courses"] == pytest.approx(2.0)

    def test_gpa_trend_with_fewer_than_trend_window_terms(self):
        # 1 term only → slice truncates gracefully; trend = that term - cumulative
        # If cumulative == that term, trend = 0
        raw = RawFeatureInputs(
            cumulative_gpa=3.5,
            recent_term_gpas=[3.5],   # only 1 term
            num_failures=0, num_repeats=0,
            completed_credits=15.0, required_credits=120.0,
            terms_elapsed=1,
            plan_courses=[],
        )
        f = compute_features(raw)
        assert f["gpa_trend"] == pytest.approx(0.0)

    def test_gpa_trend_with_no_terms(self):
        raw = RawFeatureInputs(
            cumulative_gpa=0.0,
            recent_term_gpas=[],
            num_failures=0, num_repeats=0,
            completed_credits=0.0, required_credits=120.0,
            terms_elapsed=0,
            plan_courses=[],
        )
        f = compute_features(raw)
        assert f["gpa_trend"] == pytest.approx(0.0)
        assert f["progress_rate"] == pytest.approx(0.0)
        assert f["pct_complete"] == pytest.approx(0.0)


class TestFeature8WorkloadParity:
    """Feature 8 must equal workload.raw_workload_index for the same courses."""

    def test_feature8_equals_raw_workload_index(self):
        plan_courses = [(3, 4), (3, 5), (4, 3)]
        expected_idx = raw_workload_index(plan_courses)  # 12+15+12=39

        raw = RawFeatureInputs(
            cumulative_gpa=3.0, recent_term_gpas=[3.0, 3.0],
            num_failures=0, num_repeats=0,
            completed_credits=30.0, required_credits=120.0,
            terms_elapsed=2,
            plan_courses=plan_courses,
        )
        f = compute_features(raw)
        assert f["planned_workload_index"] == pytest.approx(expected_idx)

    def test_feature8_via_engine_build_risk_inputs(self):
        """build_risk_inputs → compute_features feature8 == raw_workload_index."""
        catalog = {
            "A": _course("A", 3, 4),
            "B": _course("B", 3, 5),
        }
        plan_term = PlanTerm(term=Term.FALL, year=2024, course_codes=["A", "B"])
        a_result = _audit()

        raw = build_risk_inputs(a_result, plan_term, catalog)
        features = compute_features(raw)

        expected = raw_workload_index([(3, 4), (3, 5)])  # 12+15=27
        assert features["planned_workload_index"] == pytest.approx(expected)

    def test_score_plan_term_vector_length(self):
        catalog = {"X": _course("X", 3, 5)}
        plan_term = PlanTerm(term=Term.FALL, year=2024, course_codes=["X"])
        feat, vec = score_plan_term(_audit(), plan_term, catalog)
        assert isinstance(vec, np.ndarray)
        assert vec.shape == (9,)
        assert len(feat) == 9
