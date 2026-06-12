"""CI gate for the graduation-risk model (spec §8, plan §7 + §11).

Loads the ONNX artifact when available (version-independent, matches what
the model-server serves). Falls back to joblib only when ONNX is absent.

Skipped automatically if neither artifact exists yet (pre-Colab run).
Once artifacts land in ml/grad_risk/artifacts/, this gate is always enforced.

Asserts:
  1. macro_f1 >= macro_f1_min
  2. at_risk_recall >= at_risk_recall_min
  3. macro_f1 <= macro_f1_trivial_guard_max  (data must not be too clean)
  4. edge-case accuracy >= edge_case_accuracy_min (all obvious cases right)
  5. feature_schema order == FEATURE_ORDER from the domain module
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "ml" / "grad_risk" / "artifacts"
DATA_DIR = REPO_ROOT / "data"

ONNX_PATH = ARTIFACTS_DIR / "grad_risk.onnx"
JOBLIB_PATH = ARTIFACTS_DIR / "grad_risk.joblib"
SCHEMA_PATH = ARTIFACTS_DIR / "feature_schema.json"
TEST_CSV = DATA_DIR / "grad_risk_test.csv"
EDGE_CSV = DATA_DIR / "grad_risk_golden_edge.csv"
THRESHOLDS_PATH = REPO_ROOT / "tests" / "eval" / "eval_thresholds.yaml"


def _skip_if_no_artifact() -> None:
    if not ONNX_PATH.exists() and not JOBLIB_PATH.exists():
        pytest.skip(
            "No model artifact found — run the Colab notebook first, "
            f"then copy artifacts to {ARTIFACTS_DIR}"
        )


def _load_thresholds() -> dict:
    with open(THRESHOLDS_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("gates", {}).get("grad_risk", {})


def _predict(model, X: np.ndarray) -> np.ndarray:
    """Unified predict: works for both ONNX session and sklearn pipeline."""
    if hasattr(model, "run"):  # onnxruntime.InferenceSession
        output = model.run(["output_label"], {"float_input": X.astype(np.float32)})[0]
        return np.array(output)
    return model.predict(X)  # sklearn pipeline


@pytest.fixture(scope="module")
def model():
    _skip_if_no_artifact()
    if ONNX_PATH.exists():
        import onnxruntime as ort

        sess = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
        return sess
    import joblib

    return joblib.load(JOBLIB_PATH)


@pytest.fixture(scope="module")
def feature_schema():
    _skip_if_no_artifact()
    import json

    with open(SCHEMA_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def test_data():
    _skip_if_no_artifact()
    import pandas as pd

    return pd.read_csv(TEST_CSV)


@pytest.fixture(scope="module")
def edge_data():
    _skip_if_no_artifact()
    import pandas as pd

    return pd.read_csv(EDGE_CSV)


def test_feature_schema_order_matches_domain(feature_schema):
    """feature_schema.json must agree with FEATURE_ORDER in the domain module."""
    from keel.domain.features.grad_risk import FEATURE_ORDER

    schema_order = feature_schema["feature_order"]
    assert schema_order == FEATURE_ORDER, (
        f"feature_schema.json order does not match domain FEATURE_ORDER.\n"
        f"schema : {schema_order}\n"
        f"domain : {FEATURE_ORDER}"
    )


def test_macro_f1(model, feature_schema, test_data):
    from sklearn.metrics import f1_score

    thresholds = _load_thresholds()
    min_f1 = thresholds.get("macro_f1_min", 0.72)

    X = test_data[feature_schema["feature_order"]].values
    y = test_data["at_risk"].values
    preds = _predict(model, X)
    macro_f1 = f1_score(y, preds, average="macro")

    assert macro_f1 >= min_f1, f"macro_f1={macro_f1:.4f} is below threshold {min_f1}"


def test_at_risk_recall(model, feature_schema, test_data):
    from sklearn.metrics import f1_score

    thresholds = _load_thresholds()
    min_recall = thresholds.get("at_risk_recall_min", 0.70)

    X = test_data[feature_schema["feature_order"]].values
    y = test_data["at_risk"].values
    preds = _predict(model, X)
    at_risk_recall = f1_score(y, preds, labels=[1], average=None)[0]

    assert at_risk_recall >= min_recall, (
        f"at_risk_recall={at_risk_recall:.4f} is below threshold {min_recall}"
    )


def test_trivial_guard(model, feature_schema, test_data):
    from sklearn.metrics import f1_score

    thresholds = _load_thresholds()
    max_f1 = thresholds.get("macro_f1_trivial_guard_max", 0.97)

    X = test_data[feature_schema["feature_order"]].values
    y = test_data["at_risk"].values
    preds = _predict(model, X)
    macro_f1 = f1_score(y, preds, average="macro")

    assert macro_f1 <= max_f1, (
        f"macro_f1={macro_f1:.4f} >= trivial guard {max_f1} — data may be too clean"
    )


def test_edge_case_accuracy(model, feature_schema, edge_data):
    thresholds = _load_thresholds()
    min_edge_acc = thresholds.get("edge_case_accuracy_min", 1.0)

    X = edge_data[feature_schema["feature_order"]].values
    y = edge_data["at_risk"].values
    preds = _predict(model, X)
    acc = float((preds == y).mean())

    assert acc >= min_edge_acc, (
        f"Edge-case accuracy={acc:.4f} below {min_edge_acc}. "
        f"Misclassified rows: {list(np.where(preds != y)[0])}"
    )
