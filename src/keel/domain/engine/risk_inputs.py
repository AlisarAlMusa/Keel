"""Assemble RawFeatureInputs for the graduation-risk model.

The engine's job is to collect the raw inputs from the audit result and the
candidate plan, then hand them to the shared grad_risk module. The engine does
NOT re-implement the feature math — that lives in domain/features/grad_risk.py
and is what the offline generator used to train the model.

Source of truth: specs/004-phase-1-engine/spec.md §4.7; plan.md §3.
"""

from __future__ import annotations

import numpy as np

from keel.domain.engine.contracts import AuditResult, PlanTerm
from keel.domain.features.grad_risk import (
    FEATURE_ORDER as FEATURE_ORDER,  # re-exported so the guard test can assert identity
)
from keel.domain.features.grad_risk import (
    RawFeatureInputs,
    compute_features,
    to_vector,
)
from keel.domain.models import Course


def build_risk_inputs(
    audit: AuditResult,
    plan_term: PlanTerm,
    catalog: dict[str, Course],
) -> RawFeatureInputs:
    """Build RawFeatureInputs from an AuditResult + one candidate term.

    Parameters
    ----------
    audit:
        Output of the degree audit — provides all student-state fields.
    plan_term:
        The ONE term being scored. Only courses in this term go into
        plan_courses (spec §4.7 "one proposed term only").
    catalog:
        Course catalog — used to look up credits and difficulty.

    Returns
    -------
    RawFeatureInputs ready to pass to grad_risk.compute_features.
    """
    plan_courses: list[tuple[int, int]] = [
        (catalog[code].credits, catalog[code].difficulty)
        for code in plan_term.course_codes
        if code in catalog
    ]

    return RawFeatureInputs(
        cumulative_gpa=audit.cumulative_gpa,
        recent_term_gpas=list(audit.recent_term_gpas),
        num_failures=audit.num_failures,
        num_repeats=audit.num_repeats,
        completed_credits=audit.credits_completed,
        required_credits=audit.total_credits_required,
        terms_elapsed=audit.terms_elapsed,
        plan_courses=plan_courses,
    )


def score_plan_term(
    audit: AuditResult,
    plan_term: PlanTerm,
    catalog: dict[str, Course],
) -> tuple[dict[str, float], np.ndarray]:
    """Convenience wrapper: build inputs → compute features → return dict + vector.

    The model-server call (Phase 2) receives the vector. The feature dict is
    used to extract top contributors for the LLM mitigation.

    Returns (feature_dict, feature_vector).
    """
    raw = build_risk_inputs(audit, plan_term, catalog)
    features = compute_features(raw)
    vector = to_vector(features)
    return features, vector
