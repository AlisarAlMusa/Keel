"""Download the production model artifacts for every Keel model from MLflow.

For each model in ``MODELS`` this resolves ``<name> @ production`` in the MLflow
model registry, finds the source training run, and downloads that run's whole
``artifacts/`` directory to ``<dest>/<subdir>/artifacts/``. After download it
verifies the model's *required* files are present (a missing *optional* file —
e.g. an ONNX export that only exists when a different model family won — is fine).

Usage (local):
    MLFLOW_TRACKING_URI=http://localhost:5001 uv run python scripts/pull_model_artifacts.py

Usage (docker compose one-shot service):
    uv run python scripts/pull_model_artifacts.py --dest /app/ml

Environment variables (all required when artifacts live in MinIO):
    MLFLOW_TRACKING_URI      - e.g. http://mlflow:5000 (or ngrok URL for Colab)
    MLFLOW_S3_ENDPOINT_URL   - e.g. http://minio:9000
    AWS_ACCESS_KEY_ID        - MinIO root user
    AWS_SECRET_ACCESS_KEY    - MinIO root password
    AWS_DEFAULT_REGION       - any value, e.g. us-east-1

The script exits non-zero if any model fails (model not found, MLflow
unreachable, or a required artifact missing) so docker compose surfaces the
error. Re-running is safe: files are overwritten in place.

``--allow-missing`` (used by the docker compose sync service) downgrades a model
that is *not registered yet* to a skip instead of a failure — the bootstrap / CI
case where MLflow is fresh and empty. A model that IS registered but downloads
badly (missing required artifact), and a genuine MLflow outage, still fail even
with this flag.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODEL_ALIAS = "production"


@dataclass(frozen=True)
class ModelSpec:
    """One registered model to sync from MLflow."""

    registry_name: str  # MLflow registered-model name
    dest_subdir: str  # <dest>/<dest_subdir>/artifacts/
    required: list[str]  # filenames that MUST exist after download
    optional: list[str] = field(default_factory=list)  # nice-to-have, never fails


# The two trained Keel models. The intent winner is currently Model A
# (TF-IDF + LR); model_b.onnx only exists when DistilBERT wins, so it is
# optional and skipped gracefully when absent.
MODELS: list[ModelSpec] = [
    ModelSpec(
        registry_name="keel-grad-risk",
        dest_subdir="grad_risk",
        required=[
            "grad_risk.joblib",
            "feature_schema.json",
            "model_card.md",
            "eval_report.json",
        ],
        optional=["grad_risk.onnx"],
    ),
    ModelSpec(
        registry_name="keel-intent-router",
        dest_subdir="intent",
        required=[
            "model_a.joblib",
            "label_map.json",
            "router_config.json",
        ],
        optional=["metrics.json", "model_b.onnx"],
    ),
]


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"ERROR: environment variable {name!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def _looks_not_registered(exc: Exception) -> bool:
    """True if the resolve error means 'no such model/alias' (vs MLflow down)."""
    msg = str(exc).lower()
    return any(
        phrase in msg
        for phrase in ("does not exist", "resource_does_not_exist", "not found", "no versions")
    )


def _pull_one(client: Any, spec: ModelSpec, dest: Path, allow_missing: bool) -> list[str]:
    """Sync one model. Returns a list of error strings (empty = success/skip)."""
    print(f"\n── {spec.registry_name} @ {MODEL_ALIAS} ──────────────────────────────")

    try:
        version = client.get_model_version_by_alias(spec.registry_name, MODEL_ALIAS)
    except Exception as exc:
        if allow_missing and _looks_not_registered(exc):
            print("  - not registered yet; skipping  [allow-missing]")
            return []
        return [f"{spec.registry_name}: could not resolve @ {MODEL_ALIAS}: {exc}"]

    run_id = version.run_id
    print(f"  version {version.version}  run={run_id}")

    # Download the whole artifacts/ dir → <dest>/<subdir>/artifacts/.
    model_root = dest / spec.dest_subdir
    artifacts_dir = model_root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        client.download_artifacts(run_id, "artifacts", str(model_root))
    except Exception as exc:
        return [f"{spec.registry_name}: artifact download failed: {exc}"]

    # Report what landed; verify required files are present.
    errors: list[str] = []
    for name in spec.required:
        path = artifacts_dir / name
        if path.exists():
            print(f"  ✓ {name:<24} ({path.stat().st_size:,} bytes)")
        else:
            errors.append(f"{spec.registry_name}: required artifact missing: {name}")
            print(f"  ✗ {name:<24} MISSING (required)", file=sys.stderr)

    for name in spec.optional:
        path = artifacts_dir / name
        if path.exists():
            print(f"  ✓ {name:<24} ({path.stat().st_size:,} bytes)  [optional]")
        else:
            print(f"  - {name:<24} not present  [optional, skipped]")

    return errors


def main(dest: Path, allow_missing: bool) -> None:
    _require_env("MLFLOW_TRACKING_URI")

    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError:
        print("ERROR: mlflow is not installed. Run `uv sync` first.", file=sys.stderr)
        sys.exit(1)

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    client = MlflowClient()

    all_errors: list[str] = []
    for spec in MODELS:
        all_errors.extend(_pull_one(client, spec, dest, allow_missing))

    if all_errors:
        print(f"\n{len(all_errors)} error(s):", file=sys.stderr)
        for e in all_errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nAll models synced → {dest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull production model artifacts from MLflow.")
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "ml",
        help="Destination base directory (default: <repo_root>/ml)",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Skip (don't fail on) models not yet registered — for bootstrap / CI.",
    )
    args = parser.parse_args()
    main(args.dest, args.allow_missing)
