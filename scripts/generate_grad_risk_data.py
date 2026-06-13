"""Generate synthetic graduation-risk training data (spec §5-6, plan §3).

Writes three CSV files to <repo_root>/data/:
  grad_risk.csv              ~4 000 rows — full training set
  grad_risk_golden_edge.csv  ~24 rows  — obvious cases for the CI edge gate
  hand_labeled_slice.csv     ~20 rows  — for manual labeling exercise

Run from the repo root:
  uv run python scripts/generate_synthetic_data.py

The risk-function weights below are copied verbatim into DATA.md so the
synthetic-data assumptions are fully documented (spec §5 honesty rules).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root without installing; the package is also
# importable once `uv sync` has run (editable install in .venv).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ast
import csv
import json
from typing import Any

import numpy as np

from keel.domain.features.grad_risk import (
    FEATURE_ORDER,
    RawFeatureInputs,
    compute_features,
)


# ── Seed catalog (extracted from seed.py — no DB or module import needed) ──
# We parse the _COURSES list with ast so we get the same credits+difficulty
# values without triggering seed.py's heavy top-level imports (sqlalchemy etc.)
def _load_catalog() -> list[tuple[int, int]]:
    seed_path = Path(__file__).resolve().parent / "seed.py"
    tree = ast.parse(seed_path.read_text())
    for node in ast.walk(tree):
        # _COURSES is an annotated assignment: _COURSES: list[...] = [...]
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_COURSES"
            and node.value is not None
        ):
            courses: list[dict[str, Any]] = ast.literal_eval(node.value)
            return [(int(c["credits"]), int(c["difficulty"])) for c in courses]
    raise RuntimeError("_COURSES not found in seed.py")


CATALOG: list[tuple[int, int]] = _load_catalog()

# ── Global constants ───────────────────────────────────────────────────────
SEED = 42
N_ROWS = 4_000
REQUIRED_CREDITS = 120.0

# ── Risk-function weights (frozen; copy into DATA.md) ─────────────────────
# Weights are applied to z-scored features.
W = {
    "intercept": 0.0,  # binary-searched below to hit 23-27% at-risk rate
    "cumulative_gpa": -1.2,
    "gpa_trend": -0.8,
    "num_failures": 1.0,
    "num_repeats": 0.5,
    "progress_rate": -0.9,
    "pct_complete": -0.3,
    "planned_credits": 0.4,
    "planned_workload_index": 0.7,
    "num_hard_courses": 0.5,
    "interaction": 0.6,  # relu(-z_gpa) * relu(z_workload) — required nonlinear term
}


def _softplus(x: np.ndarray) -> np.ndarray:
    out: np.ndarray = np.log1p(np.exp(np.clip(x, -30, 30)))
    return out


def _sigmoid(x: np.ndarray) -> np.ndarray:
    out: np.ndarray = 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))
    return out


def _sample_plan(
    rng: np.random.Generator,
    target_credits: int,
    overload_tendency: float,
) -> list[tuple[int, int]]:
    """Sample courses from CATALOG so sum(credits) ≈ target_credits.

    Harder courses appear more often when overload_tendency is high.
    """
    if not CATALOG:
        return []

    diff_arr = np.array([d for _, d in CATALOG], dtype=float)

    # Weight harder courses more when overloading
    bias = np.clip(overload_tendency, -3, 3)
    weights = np.exp(0.3 * bias * diff_arr)
    weights /= weights.sum()

    chosen: list[tuple[int, int]] = []
    total = 0
    for _ in range(100):  # safety cap
        idx = int(rng.choice(len(CATALOG), p=weights))
        cr, df = CATALOG[idx]
        if total + cr > target_credits + 3:
            continue
        chosen.append((cr, df))
        total += cr
        if total >= target_credits - 2:
            break
    return chosen if chosen else [(3, 2)]


def _generate_rows(rng: np.random.Generator, n: int) -> list[dict[str, Any]]:
    # ── Latent variables ──────────────────────────────────────────────────
    ability = rng.standard_normal(n)
    momentum = rng.standard_normal(n)
    seniority = rng.uniform(0, 1, n)
    overload_tendency = rng.normal(-0.2 * ability, 1)

    # ── Cumulative GPA ────────────────────────────────────────────────────
    cum_gpa = np.clip(2.6 + 0.55 * ability + rng.normal(0, 0.15, n), 0.0, 4.0)

    # ── Recent term GPAs (TREND_WINDOW=2) ─────────────────────────────────
    t1 = np.clip(cum_gpa + 0.4 * momentum + rng.normal(0, 0.2, n), 0.0, 4.0)
    t2 = np.clip(cum_gpa + 0.4 * momentum + rng.normal(0, 0.2, n), 0.0, 4.0)

    # ── Failures / repeats ────────────────────────────────────────────────
    failure_rate = _softplus(0.8 - 0.9 * ability)
    num_failures = rng.poisson(failure_rate).astype(int)
    # repeats are a subset of failures
    num_repeats = rng.binomial(num_failures, 0.6)

    # ── Credits ───────────────────────────────────────────────────────────
    terms_elapsed = np.round(1 + seniority * 7).astype(int)
    credit_rate = np.clip(0.75 + 0.3 * ability, 0.5, 1.1)
    completed_credits = np.round(terms_elapsed * 15 * credit_rate)

    # ── Planned credits ───────────────────────────────────────────────────
    planned_credits_raw = np.round(15 + 3 * overload_tendency)
    planned_credits = np.clip(planned_credits_raw, 9, 21).astype(int)

    rows = []
    for i in range(n):
        plan_courses = _sample_plan(rng, int(planned_credits[i]), float(overload_tendency[i]))

        raw = RawFeatureInputs(
            cumulative_gpa=float(cum_gpa[i]),
            recent_term_gpas=[float(t1[i]), float(t2[i])],
            num_failures=int(num_failures[i]),
            num_repeats=int(num_repeats[i]),
            completed_credits=float(completed_credits[i]),
            required_credits=REQUIRED_CREDITS,
            terms_elapsed=int(terms_elapsed[i]),
            plan_courses=plan_courses,
        )
        feats = compute_features(raw)
        rows.append(feats)
    return rows


def _compute_labels(
    rows: list[dict[str, Any]], intercept: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply the risk function and sample binary labels."""
    mat = np.array([[r[f] for f in FEATURE_ORDER] for r in rows])

    # Standardize
    mu = mat.mean(axis=0)
    sigma = mat.std(axis=0)
    sigma = np.where(sigma < 1e-8, 1.0, sigma)
    z = (mat - mu) / sigma

    feat_idx = {name: i for i, name in enumerate(FEATURE_ORDER)}
    ig = feat_idx["cumulative_gpa"]
    iw = feat_idx["planned_workload_index"]

    interaction = np.maximum(-z[:, ig], 0) * np.maximum(z[:, iw], 0)

    logit = (
        intercept
        + W["cumulative_gpa"] * z[:, feat_idx["cumulative_gpa"]]
        + W["gpa_trend"] * z[:, feat_idx["gpa_trend"]]
        + W["num_failures"] * z[:, feat_idx["num_failures"]]
        + W["num_repeats"] * z[:, feat_idx["num_repeats"]]
        + W["progress_rate"] * z[:, feat_idx["progress_rate"]]
        + W["pct_complete"] * z[:, feat_idx["pct_complete"]]
        + W["planned_credits"] * z[:, feat_idx["planned_credits"]]
        + W["planned_workload_index"] * z[:, feat_idx["planned_workload_index"]]
        + W["num_hard_courses"] * z[:, feat_idx["num_hard_courses"]]
        + W["interaction"] * interaction
    )

    p = _sigmoid(logit)
    return rng.binomial(1, p).astype(int), mu, sigma


def _binary_search_intercept(rows: list[dict[str, Any]], rng: np.random.Generator) -> float:
    """Find intercept so at-risk rate is 23–27%."""
    target_lo, target_hi = 0.23, 0.27
    lo, hi = -10.0, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        labels, _, _ = _compute_labels(rows, mid, np.random.default_rng(SEED))
        rate = labels.mean()
        if rate < target_lo:
            lo = mid  # too few at-risk → raise intercept
        elif rate > target_hi:
            hi = mid  # too many at-risk → lower intercept
        else:
            return mid
    return mid


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _golden_edge_rows() -> list[dict[str, Any]]:
    """Hand-constructed obvious at-risk and on-track cases."""
    cases: list[dict[str, Any]] = []

    # ── Clear at-risk: failing GPA, many failures, heavy overloaded plan ──
    for i in range(12):
        raw = RawFeatureInputs(
            cumulative_gpa=1.5 + i * 0.02,
            recent_term_gpas=[1.2, 1.1],
            num_failures=4 + (i % 3),
            num_repeats=2,
            completed_credits=20.0 + i,
            required_credits=120.0,
            terms_elapsed=4,
            plan_courses=[(4, 5), (4, 5), (3, 4), (3, 4)],
        )
        row: dict[str, Any] = dict(compute_features(raw))
        row["at_risk"] = 1
        row["row_id"] = f"edge_risk_{i:02d}"
        cases.append(row)

    # ── Clear on-track: strong GPA, no failures, light plan ───────────────
    for i in range(12):
        raw = RawFeatureInputs(
            cumulative_gpa=3.7 + i * 0.01,
            recent_term_gpas=[3.8, 3.9],
            num_failures=0,
            num_repeats=0,
            completed_credits=90.0 + i,
            required_credits=120.0,
            terms_elapsed=6,
            plan_courses=[(3, 2), (3, 2), (3, 1)],
        )
        row = dict(compute_features(raw))
        row["at_risk"] = 0
        row["row_id"] = f"edge_ok_{i:02d}"
        cases.append(row)

    return cases


def main() -> None:
    rng = np.random.default_rng(SEED)
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = repo_root / "data"

    print("Generating rows ...")
    rows = _generate_rows(rng, N_ROWS)

    print("Searching for intercept (target at-risk rate 23–27%) ...")
    intercept = _binary_search_intercept(rows, rng)
    labels, mu, sigma = _compute_labels(rows, intercept, np.random.default_rng(SEED))

    at_risk_rate = labels.mean()
    print(f"Intercept   : {intercept:.4f}")
    print(f"At-risk rate: {at_risk_rate:.3f}  ({labels.sum()} / {len(labels)})")
    assert 0.23 <= at_risk_rate <= 0.27, (
        f"At-risk rate {at_risk_rate:.3f} outside 23–27% target. Adjust the binary search bounds."
    )

    # ── Full dataset ──────────────────────────────────────────────────────
    fieldnames = ["row_id"] + FEATURE_ORDER + ["at_risk"]
    full_rows = [{"row_id": i, **r, "at_risk": int(labels[i])} for i, r in enumerate(rows)]
    out_full = data_dir / "grad_risk.csv"
    _write_csv(out_full, fieldnames, full_rows)
    print(f"Written: {out_full}  ({len(full_rows)} rows)")

    # ── Class means (sanity check) ────────────────────────────────────────
    import statistics

    print("\nFeature means by class:")
    for feat in FEATURE_ORDER:
        vals_risk = [r[feat] for r, lbl in zip(rows, labels, strict=True) if lbl == 1]
        vals_ok = [r[feat] for r, lbl in zip(rows, labels, strict=True) if lbl == 0]
        print(
            f"  {feat:<26}  at-risk={statistics.mean(vals_risk):.3f}"
            f"  on-track={statistics.mean(vals_ok):.3f}"
        )

    # ── Golden edge set ───────────────────────────────────────────────────
    edge_rows = _golden_edge_rows()
    edge_fieldnames = ["row_id"] + FEATURE_ORDER + ["at_risk"]
    out_edge = data_dir / "grad_risk_golden_edge.csv"
    _write_csv(out_edge, edge_fieldnames, edge_rows)
    print(f"\nWritten: {out_edge}  ({len(edge_rows)} rows)")

    # ── Hand-labeled slice ────────────────────────────────────────────────
    # 20 rows from the full set sampled to be roughly balanced
    risk_indices = [i for i, lbl in enumerate(labels) if lbl == 1][:10]
    ok_indices = [i for i, lbl in enumerate(labels) if lbl == 0][:10]
    slice_indices = sorted(risk_indices + ok_indices)

    hand_rows = [
        {
            "row_id": full_rows[i]["row_id"],
            **{f: full_rows[i][f] for f in FEATURE_ORDER},
            "generator_label": full_rows[i]["at_risk"],
            "human_label": "",
        }
        for i in slice_indices
    ]
    hand_fieldnames = ["row_id"] + FEATURE_ORDER + ["generator_label", "human_label"]
    out_hand = data_dir / "hand_labeled_slice.csv"
    _write_csv(out_hand, hand_fieldnames, hand_rows)
    print(f"Written: {out_hand}  ({len(hand_rows)} rows, human_label column empty)")

    # ── Save intercept + weights for DATA.md reference ────────────────────
    meta = {
        "intercept": round(intercept, 6),
        "weights": W,
        "at_risk_rate": round(float(at_risk_rate), 4),
        "n_rows": N_ROWS,
        "seed": SEED,
        "feature_order": FEATURE_ORDER,
        "standardization": {
            "mu": [round(float(v), 6) for v in mu],
            "sigma": [round(float(v), 6) for v in sigma],
        },
    }
    meta_path = data_dir / "grad_risk_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"Written: {meta_path}  (weights + intercept for DATA.md)")


if __name__ == "__main__":
    main()
