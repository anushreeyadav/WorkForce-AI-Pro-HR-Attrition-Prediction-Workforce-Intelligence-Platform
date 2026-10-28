"""Model artifact storage. Only files written by save() inside the configured directory can be loaded.

joblib uses pickle, which can execute code on load. Safety measures:
- Version names are validated against a strict pattern and resolved inside the artifact directory.
- The SHA-256 of model.joblib, recorded in the database at training time, must match before loading.
- metadata.json must declare the expected artifact format and feature-schema version.
- The loaded object must be the expected Pipeline type with the expected feature columns.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

ARTIFACT_FORMAT = 1
MODEL_FILE = "model.joblib"
METADATA_FILE = "metadata.json"
VERSION_PATTERN = re.compile(r"^lr-\d{14}-[0-9a-f]{8}$")


class ArtifactError(Exception):
    """Missing, corrupt, tampered or incompatible artifact. Messages are safe to show administrators."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_dir(base_dir: Path, version: str) -> Path:
    if not VERSION_PATTERN.fullmatch(version):
        raise ArtifactError("Invalid model version name")
    base = base_dir.resolve()
    path = (base / version).resolve()
    if path.parent != base:
        raise ArtifactError("Invalid model version name")
    return path


def save(pipeline: Pipeline, metadata: dict, base_dir: Path) -> str:
    """Write model + metadata to <base_dir>/<version>/ and return the model file's SHA-256."""
    target = version_dir(base_dir, metadata["model_version"])
    target.mkdir(parents=True, exist_ok=False)  # never overwrite an existing version
    fd, temp_name = tempfile.mkstemp(dir=target, suffix=".tmp")
    os.close(fd)
    joblib.dump(pipeline, temp_name)
    os.replace(temp_name, target / MODEL_FILE)
    (target / METADATA_FILE).write_text(
        json.dumps({**metadata, "artifact_format": ARTIFACT_FORMAT}, indent=2, sort_keys=True), encoding="utf-8"
    )
    return _sha256(target / MODEL_FILE)


def load(base_dir: Path, version: str, expected_sha256: str, feature_schema_version: str,
         feature_columns: list[str]) -> Pipeline:
    target = version_dir(base_dir, version)
    model_path, metadata_path = target / MODEL_FILE, target / METADATA_FILE
    if not model_path.is_file() or not metadata_path.is_file():
        raise ArtifactError("Model artifact files are missing")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ArtifactError("Model metadata is unreadable") from None
    if metadata.get("artifact_format") != ARTIFACT_FORMAT or metadata.get("model_version") != version:
        raise ArtifactError("Model metadata does not match the expected format or version")
    if metadata.get("feature_schema_version") != feature_schema_version:
        raise ArtifactError("Model was trained on a different feature schema version")
    # Verify integrity BEFORE unpickling, so a modified or substituted file is never loaded.
    if _sha256(model_path) != expected_sha256:
        raise ArtifactError("Model file checksum does not match the registered artifact")
    try:
        pipeline = joblib.load(model_path)
    except Exception:  # noqa: BLE001 - any failure here means an unusable artifact
        raise ArtifactError("Model file could not be loaded") from None
    if not isinstance(pipeline, Pipeline) or not isinstance(pipeline[-1], LogisticRegression):
        raise ArtifactError("Model file does not contain the expected pipeline")
    if list(getattr(pipeline, "feature_names_in_", [])) != list(feature_columns):
        raise ArtifactError("Model expects different input features")
    return pipeline
