"""Pure ML layer (no API, no database). All data here is SYNTHETIC (see tests/synthetic_attrition.py)."""

from datetime import UTC, datetime
from decimal import Decimal

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import train_test_split

from ml.attrition import artifacts, predictor
from ml.attrition.features import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    FEATURES,
    FORBIDDEN_COLUMNS,
    NUMERIC_FEATURES,
    TARGET,
    FeatureValidationError,
    describe_schema,
    missing_inputs,
    validate_frame,
)
from ml.attrition.training import TrainingConfig, dataset_fingerprint, train
from tests.synthetic_attrition import synthetic_dataset

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
THRESHOLDS = predictor.Thresholds(Decimal("40"), Decimal("70"))


@pytest.fixture(scope="module")
def trained():
    return train(synthetic_dataset(), TrainingConfig(), NOW)


def record(**overrides) -> dict:
    base = {"tenure_months": 24, "late_rate_90d": 0.1, "avg_overtime_minutes_90d": 30.0,
            "recorded_absence_days_90d": 0, "attendance_days_recorded_90d": 50, "department": "1"}
    return {**base, **overrides}


# --- Feature schema ---------------------------------------------------------------------------------


def test_schema_is_documented_and_free_of_identifiers() -> None:
    assert FEATURE_SCHEMA_VERSION == "attrition-features-v1"
    assert not set(FEATURE_COLUMNS) & FORBIDDEN_COLUMNS
    assert all(spec.rationale and spec.description for spec in FEATURES)
    assert [item["name"] for item in describe_schema()] == FEATURE_COLUMNS


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda f: f.drop(columns=["tenure_months"]), "Missing required columns: tenure_months"),
        (lambda f: f.assign(work_email="person@example.com"), "Forbidden columns"),
        (lambda f: f.assign(gender="x"), "Forbidden columns"),
        (lambda f: f.assign(employment_status="ACTIVE"), "Forbidden columns"),
        (lambda f: f.assign(favourite_colour="blue"), "Unknown columns: favourite_colour"),
        (lambda f: f.assign(late_rate_90d=1.5), "late_rate_90d: 200 value(s) above 1"),
        (lambda f: f.assign(tenure_months=-1), "tenure_months: 200 value(s) below 0"),
        (lambda f: f.assign(recorded_absence_days_90d="many"), "non-numeric"),
        (lambda f: f.assign(tenure_months=np.nan), "tenure_months: 200 missing value(s)"),
        (lambda f: f.assign(attrited=2), "not 0 or 1"),
    ],
)
def test_validation_errors(mutate, message) -> None:
    with pytest.raises(FeatureValidationError) as exc_info:
        validate_frame(mutate(synthetic_dataset()), require_target=True)
    assert message in str(exc_info.value)
    assert "person@example.com" not in str(exc_info.value)  # values are never echoed


def test_missing_values_stay_missing() -> None:
    frame = validate_frame(pd.DataFrame([record(late_rate_90d=None, department=None)]), require_target=False)
    assert pd.isna(frame.loc[0, "late_rate_90d"]) and pd.isna(frame.loc[0, "department"])
    assert missing_inputs(record(late_rate_90d=None, department=None)) == ["late_rate_90d", "department"]


def test_fingerprint_is_order_independent() -> None:
    data = validate_frame(synthetic_dataset(), require_target=True)
    shuffled = data.sample(frac=1, random_state=3)[list(reversed(data.columns))]
    assert dataset_fingerprint(data) == dataset_fingerprint(shuffled)
    changed = data.copy()
    changed.loc[0, "tenure_months"] += 1
    assert dataset_fingerprint(changed) != dataset_fingerprint(data)


# --- Training --------------------------------------------------------------------------------------


def test_training_metadata_and_metrics(trained) -> None:
    meta = trained.metadata
    assert meta["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert meta["model_version"].startswith("lr-20260301120000-")
    assert meta["rows"] == {"total": 200, "train": 150, "test": 50, "duplicates_removed": 0}
    assert meta["imbalance_handling"] == "class_weight=balanced"
    assert meta["production_validated"] is False
    metrics = meta["metrics"]
    assert set(metrics) == {"decision_threshold", "precision", "recall", "f1", "roc_auc", "confusion_matrix"}
    assert all(0 <= metrics[k] <= 1 for k in ("precision", "recall", "f1", "roc_auc"))
    assert sum(metrics["confusion_matrix"].values()) == 50
    assert trained.pipeline[-1].class_weight == "balanced"


def test_training_is_reproducible() -> None:
    first = train(synthetic_dataset(), TrainingConfig(), NOW)
    second = train(synthetic_dataset(), TrainingConfig(), NOW)
    assert first.metadata == second.metadata
    assert np.array_equal(first.pipeline[-1].coef_, second.pipeline[-1].coef_)


def test_preprocessing_fitted_on_training_split_only(trained) -> None:
    data = validate_frame(synthetic_dataset(), require_target=True)
    x_train, _x_test, _y_train, _y_test = train_test_split(
        data[FEATURE_COLUMNS], data[TARGET], test_size=0.25, stratify=data[TARGET], random_state=42)
    scaler = trained.pipeline[0].named_transformers_["num"].named_steps["scale"]
    imputer = trained.pipeline[0].named_transformers_["num"].named_steps["impute"]
    # Imputation medians come from the training rows, not the full dataset.
    assert np.allclose(imputer.statistics_, x_train[NUMERIC_FEATURES].median().to_numpy())
    assert not np.allclose(imputer.statistics_, data[NUMERIC_FEATURES].median().to_numpy())
    # Scaled values were fitted on the training split (n_samples_seen_ == training rows).
    assert int(np.atleast_1d(scaler.n_samples_seen_)[0]) == len(x_train)


def test_duplicates_removed_and_counted() -> None:
    data = synthetic_dataset()
    with_duplicates = pd.concat([data, data.head(10)], ignore_index=True)
    assert train(with_duplicates, TrainingConfig(), NOW).metadata["rows"]["duplicates_removed"] == 10


@pytest.mark.parametrize(
    "frame, message",
    [
        (synthetic_dataset(n=30), "At least 50 distinct rows"),
        (synthetic_dataset().assign(attrited=0), "Both classes are required"),
        (pd.concat([synthetic_dataset().query("attrited == 0"), synthetic_dataset().query("attrited == 1").head(5)]),
         "Each class needs at least 10 rows"),
    ],
    ids=["too-few-rows", "single-class", "tiny-minority"],
)
def test_insufficient_datasets_rejected(frame, message) -> None:
    with pytest.raises(FeatureValidationError, match=message):
        train(frame, TrainingConfig(), NOW)


# --- Artifacts --------------------------------------------------------------------------------------


def test_artifact_roundtrip(tmp_path, trained) -> None:
    sha = artifacts.save(trained.pipeline, trained.metadata, tmp_path)
    loaded = artifacts.load(tmp_path, trained.metadata["model_version"], sha, FEATURE_SCHEMA_VERSION, FEATURE_COLUMNS)
    row = validate_frame(pd.DataFrame([record()]), require_target=False)
    assert loaded.predict_proba(row)[0, 1] == trained.pipeline.predict_proba(row)[0, 1]
    with pytest.raises(FileExistsError):
        artifacts.save(trained.pipeline, trained.metadata, tmp_path)  # never overwrite a version


def test_missing_and_tampered_artifacts(tmp_path, trained) -> None:
    version = trained.metadata["model_version"]
    with pytest.raises(artifacts.ArtifactError, match="missing"):
        artifacts.load(tmp_path, version, "0" * 64, FEATURE_SCHEMA_VERSION, FEATURE_COLUMNS)
    sha = artifacts.save(trained.pipeline, trained.metadata, tmp_path)
    with (tmp_path / version / "model.joblib").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(artifacts.ArtifactError, match="checksum"):
        artifacts.load(tmp_path, version, sha, FEATURE_SCHEMA_VERSION, FEATURE_COLUMNS)


def test_incompatible_schema_rejected(tmp_path, trained) -> None:
    sha = artifacts.save(trained.pipeline, trained.metadata, tmp_path)
    with pytest.raises(artifacts.ArtifactError, match="different feature schema"):
        artifacts.load(tmp_path, trained.metadata["model_version"], sha, "attrition-features-v2", FEATURE_COLUMNS)


def test_unexpected_object_rejected_even_with_matching_checksum(tmp_path, trained) -> None:
    metadata = {**trained.metadata, "model_version": "lr-20260301120000-deadbeef"}
    sha = artifacts.save(trained.pipeline, metadata, tmp_path)
    model_file = tmp_path / metadata["model_version"] / "model.joblib"
    joblib.dump({"not": "a pipeline"}, model_file)
    sha = artifacts._sha256(model_file)
    with pytest.raises(artifacts.ArtifactError, match="expected pipeline"):
        artifacts.load(tmp_path, metadata["model_version"], sha, FEATURE_SCHEMA_VERSION, FEATURE_COLUMNS)


@pytest.mark.parametrize("version", ["../outside", "lr-1-abc", "lr-20260301120000-DEADBEEF", "C:\\models\\x", ""])
def test_invalid_version_names_rejected(tmp_path, version) -> None:
    with pytest.raises(artifacts.ArtifactError, match="Invalid model version"):
        artifacts.load(tmp_path, version, "0" * 64, FEATURE_SCHEMA_VERSION, FEATURE_COLUMNS)


# --- Scoring ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("probability, score", [(-0.1, "0.00"), (0.0, "0.00"), (0.123456, "12.35"), (0.5, "50.00"),
                                                (1.0, "100.00"), (1.7, "100.00")])
def test_score_bounds(probability, score) -> None:
    assert predictor.score_from_probability(probability) == Decimal(score)


@pytest.mark.parametrize("score, category", [("0", "LOW"), ("39.99", "LOW"), ("40", "MEDIUM"), ("69.99", "MEDIUM"),
                                             ("70", "HIGH"), ("100", "HIGH")])
def test_category_thresholds(score, category) -> None:
    assert predictor.categorize(Decimal(score), THRESHOLDS) == category


@pytest.mark.parametrize("medium, high", [("0", "50"), ("70", "40"), ("50", "50"), ("40", "101")])
def test_invalid_thresholds(medium, high) -> None:
    with pytest.raises(ValueError):
        predictor.Thresholds(Decimal(medium), Decimal(high))


def test_prediction_and_explanation(trained) -> None:
    outcome = predictor.predict(trained.pipeline, record(late_rate_90d=None), THRESHOLDS)
    assert Decimal(0) <= outcome.risk_score <= Decimal(100)
    assert outcome.risk_category == predictor.categorize(outcome.risk_score, THRESHOLDS)
    assert outcome.missing_inputs == ["late_rate_90d"]
    features = {f["feature"] for f in outcome.factors}
    assert len(outcome.factors) <= 3 and features <= set(FEATURE_COLUMNS)
    assert "late_rate_90d" not in features  # a missing input is never presented as a reason
    assert all(f["direction"] in ("raises", "lowers") and f["weight"] > 0 for f in outcome.factors)
    # Same input, same output.
    assert predictor.predict(trained.pipeline, record(late_rate_90d=None), THRESHOLDS) == outcome


def test_planted_signal_moves_score_in_expected_direction(trained) -> None:
    low = predictor.predict(trained.pipeline, record(late_rate_90d=0.0, avg_overtime_minutes_90d=0), THRESHOLDS)
    high = predictor.predict(trained.pipeline, record(late_rate_90d=0.5, avg_overtime_minutes_90d=120), THRESHOLDS)
    assert high.risk_score > low.risk_score


def test_invalid_prediction_input_rejected(trained) -> None:
    with pytest.raises(FeatureValidationError):
        predictor.predict(trained.pipeline, record(tenure_months=-3), THRESHOLDS)
