"""Download the production grad-risk model artifacts from MLflow.

Resolves ``keel-grad-risk @ production`` in the MLflow model registry,
finds the source training run, and downloads its artifacts to
``<dest>/grad_risk/artifacts/``.

Usage (local):
    uv run python scripts/pull_model_artifacts.py

Usage (docker compose one-shot service):
    uv run python scripts/pull_model_artifacts.py --dest /app/ml

Environment variables (all required when artifacts live in MinIO):
    MLFLOW_TRACKING_URI      - e.g. http://mlflow:5000 (or ngrok URL for Colab)
    MLFLOW_S3_ENDPOINT_URL   - e.g. http://minio:9000
    AWS_ACCESS_KEY_ID        - MinIO root user
    AWS_SECRET_ACCESS_KEY    - MinIO root password
    AWS_DEFAULT_REGION       - any value, e.g. us-east-1

The script exits non-zero on any failure so docker compose surfaces the error.
Re-running is safe: files are overwritten in place.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Artifacts to pull from the run (relative paths inside the MLflow run).
# grad_risk.onnx is pulled only if it exists — skipped gracefully otherwise.
_REQUIRED_ARTIFACTS = [
    "artifacts/grad_risk.joblib",
    "artifacts/feature_schema.json",
    "artifacts/model_card.md",
    "artifacts/eval_report.json",
]
_OPTIONAL_ARTIFACTS = [
    "artifacts/grad_risk.onnx",
]

MODEL_NAME = "keel-grad-risk"
MODEL_ALIAS = "production"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"ERROR: environment variable {name!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def main(dest: Path) -> None:
    tracking_uri = _require_env("MLFLOW_TRACKING_URI")

    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError:
        print("ERROR: mlflow is not installed. Run `uv sync` first.", file=sys.stderr)
        sys.exit(1)

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()

    # ── Resolve production alias → version → run_id ──────────────────────
    try:
        version = client.get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
    except Exception as exc:
        print(
            f"ERROR: could not resolve {MODEL_NAME!r} @ {MODEL_ALIAS!r}.\n"
            f"  Tracking URI : {tracking_uri}\n"
            f"  Cause        : {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    run_id = version.run_id
    print(f"Resolved {MODEL_NAME} @ {MODEL_ALIAS} → version {version.version}  run={run_id}")

    # ── Destination directory ─────────────────────────────────────────────
    artifacts_dir = dest / "grad_risk" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ── Download required artifacts ───────────────────────────────────────
    errors: list[str] = []
    downloaded: list[str] = []

    for artifact_path in _REQUIRED_ARTIFACTS:
        filename = Path(artifact_path).name
        local_path = artifacts_dir / filename
        try:
            client.download_artifacts(run_id, artifact_path, str(artifacts_dir))
            # MLflow downloads into a subdirectory matching the artifact path;
            # move it to the flat artifacts_dir if needed.
            nested = artifacts_dir / artifact_path
            if nested.exists() and nested != local_path:
                local_path.write_bytes(nested.read_bytes())
                # clean up nested dirs created by MLflow
                _cleanup_nested(artifacts_dir, artifact_path)
            size = local_path.stat().st_size if local_path.exists() else "?"
            print(f"  ✓ {filename:<30} ({size} bytes)")
            downloaded.append(filename)
        except Exception as exc:
            errors.append(f"{artifact_path}: {exc}")
            print(f"  ✗ {filename}: {exc}", file=sys.stderr)

    # ── Download optional artifacts (best-effort) ─────────────────────────
    for artifact_path in _OPTIONAL_ARTIFACTS:
        filename = Path(artifact_path).name
        local_path = artifacts_dir / filename
        try:
            client.download_artifacts(run_id, artifact_path, str(artifacts_dir))
            nested = artifacts_dir / artifact_path
            if nested.exists() and nested != local_path:
                local_path.write_bytes(nested.read_bytes())
                _cleanup_nested(artifacts_dir, artifact_path)
            size = local_path.stat().st_size if local_path.exists() else "?"
            print(f"  ✓ {filename:<30} ({size} bytes)  [optional]")
            downloaded.append(filename)
        except Exception as exc:
            print(f"  - {filename}: not found or skipped ({exc.__class__.__name__})")

    # ── Result ────────────────────────────────────────────────────────────
    print(f"\nDownloaded {len(downloaded)} artifact(s) → {artifacts_dir}")

    if errors:
        print(f"\n{len(errors)} required artifact(s) failed:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)


def _cleanup_nested(base: Path, artifact_path: str) -> None:
    """Remove the top-level subdirectory MLflow creates (e.g. base/artifacts/)."""
    top = base / artifact_path.split("/")[0]
    if top.is_dir():
        import shutil
        shutil.rmtree(top, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull production grad-risk artifacts from MLflow.")
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "ml",
        help="Destination base directory (default: <repo_root>/ml)",
    )
    args = parser.parse_args()
    main(args.dest)
