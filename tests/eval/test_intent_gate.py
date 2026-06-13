"""CI gate for the intent classifier / router (spec §7, plan §7).

Loads the production model (currently Model A = TF-IDF + Logistic Regression,
a joblib) and the held-out test split — reconstructed from intent_dataset.csv +
intent-split.json["test"] — then asserts the headline macro-F1 and the routing
policy (accuracy on the covered subset at the router_config threshold) both hold.

Skipped automatically if the model artifact is absent (pre-pull). Once
ml/intent/artifacts/model_a.joblib lands, this gate is always enforced.

Note on serving: sklearn sorts string classes, so model.classes_ is alphabetical
and is NOT label_map.json's id2label order. predict() returns string labels
directly (used here); serving must map a probability index via model.classes_,
never label_map.id2label.

Asserts:
  1. label_map order == LABELS in the generator (frozen-contract check)
  2. model.classes_ cover exactly the 15 labels
  3. macro_f1 >= macro_f1_min
  4. covered accuracy >= covered_accuracy_min at the router threshold
  5. macro_f1 <= macro_f1_trivial_guard_max  (data must not be trivially clean)
  6. 100% accuracy on the held-out golden set (obvious canonical case per label)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "ml" / "intent" / "artifacts"
DATA_DIR = REPO_ROOT / "data"

MODEL_PATH = ARTIFACTS_DIR / "model_a.joblib"
LABEL_MAP_PATH = ARTIFACTS_DIR / "label_map.json"
ROUTER_CFG_PATH = ARTIFACTS_DIR / "router_config.json"
DATASET_CSV = DATA_DIR / "intent_dataset.csv"
SPLIT_JSON = DATA_DIR / "intent-split.json"
GOLDEN_CSV = DATA_DIR / "intent_golden.csv"
THRESHOLDS_PATH = REPO_ROOT / "tests" / "eval" / "eval_thresholds.yaml"


def _skip_if_no_artifact() -> None:
    if not MODEL_PATH.exists():
        pytest.skip(
            "No intent model artifact found — run the Colab notebook + "
            f"scripts/pull_model_artifacts.py first ({MODEL_PATH})"
        )


def _load_thresholds() -> dict:
    with open(THRESHOLDS_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("gates", {}).get("intent", {})


@pytest.fixture(scope="module")
def model():
    _skip_if_no_artifact()
    import joblib

    return joblib.load(MODEL_PATH)


@pytest.fixture(scope="module")
def label_map() -> dict:
    _skip_if_no_artifact()
    with open(LABEL_MAP_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def router_config() -> dict:
    _skip_if_no_artifact()
    with open(ROUTER_CFG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def test_data():
    """Reconstruct the held-out test split from the dataset + split indices."""
    _skip_if_no_artifact()
    import pandas as pd

    df = pd.read_csv(DATASET_CSV)
    split = json.loads(SPLIT_JSON.read_text())
    return df.iloc[split["test"]].reset_index(drop=True)


@pytest.fixture(scope="module")
def golden_data():
    """Held-out obvious canonical cases (one per label, x2) — must all be right."""
    _skip_if_no_artifact()
    import pandas as pd

    return pd.read_csv(GOLDEN_CSV)


def test_label_map_matches_generator(label_map):
    """label_map.json label order must equal the frozen LABELS in the generator."""
    from scripts.generate_intent_dataset import LABELS

    assert label_map["labels"] == LABELS, (
        "label_map.json order does not match the generator's LABELS.\n"
        f"label_map: {label_map['labels']}\n"
        f"generator: {LABELS}"
    )


def test_model_classes_cover_labels(model, label_map):
    """The trained model's classes must be exactly the 15 contract labels."""
    assert set(map(str, model.classes_)) == set(label_map["labels"])


def test_macro_f1(model, test_data):
    from sklearn.metrics import f1_score

    thresholds = _load_thresholds()
    min_f1 = thresholds.get("macro_f1_min", 0.72)

    preds = model.predict(test_data["text"].tolist())
    macro_f1 = f1_score(test_data["label"].tolist(), preds, average="macro")

    assert macro_f1 >= min_f1, f"macro_f1={macro_f1:.4f} is below threshold {min_f1}"


def test_routing_coverage(model, test_data, router_config):
    """At the router threshold, accuracy on the covered subset must hold."""
    from sklearn.metrics import accuracy_score

    thresholds = _load_thresholds()
    min_cov_acc = thresholds.get("covered_accuracy_min", 0.85)

    threshold = router_config["fallback_threshold"]
    texts = test_data["text"].tolist()
    y = np.array(test_data["label"].tolist())

    preds = np.array(model.predict(texts))
    max_prob = model.predict_proba(texts).max(axis=1)
    covered = max_prob >= threshold

    assert covered.any(), f"No test message reaches the router threshold {threshold}"
    covered_acc = accuracy_score(y[covered], preds[covered])

    assert covered_acc >= min_cov_acc, (
        f"covered_accuracy={covered_acc:.4f} (coverage={covered.mean():.2%} "
        f"at threshold={threshold:.4f}) is below {min_cov_acc}"
    )


def test_trivial_guard(model, test_data):
    from sklearn.metrics import f1_score

    thresholds = _load_thresholds()
    max_f1 = thresholds.get("macro_f1_trivial_guard_max", 0.99)

    preds = model.predict(test_data["text"].tolist())
    macro_f1 = f1_score(test_data["label"].tolist(), preds, average="macro")

    assert macro_f1 <= max_f1, (
        f"macro_f1={macro_f1:.4f} >= trivial guard {max_f1} — data may be too clean"
    )


def test_golden_accuracy(model, golden_data):
    """Every obvious, held-out golden case must be classified correctly."""
    thresholds = _load_thresholds()
    min_acc = thresholds.get("golden_accuracy_min", 1.0)

    preds = np.array(model.predict(golden_data["text"].tolist()))
    y = np.array(golden_data["label"].tolist())
    acc = float((preds == y).mean())

    misses = [
        f"{golden_data['text'][i]!r} (true={y[i]}, pred={preds[i]})"
        for i in np.where(preds != y)[0]
    ]
    assert acc >= min_acc, f"Golden accuracy={acc:.4f} below {min_acc}. Misclassified: {misses}"
