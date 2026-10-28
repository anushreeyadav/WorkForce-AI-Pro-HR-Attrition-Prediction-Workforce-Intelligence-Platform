"""Reproducible baseline training: validated data -> stratified split -> pipeline -> held-out metrics.

Model: logistic regression (interpretable coefficients) with class_weight="balanced" to counter class
imbalance by re-weighting rather than resampling. All preprocessing lives inside the Pipeline and is
fitted on the training split only, so the test split never influences scaling or imputation.
"""

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.attrition.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    NUMERIC_FEATURES,
    TARGET,
    FeatureValidationError,
    validate_frame,
)

MODEL_FAMILY = "logistic_regression"
DECISION_THRESHOLD = 0.5  # used for precision/recall/F1/confusion matrix only
CALIBRATION_BINS = 5
METRICS_SCOPE = ("Held-out split of the training dataset. Describes that dataset only; not evidence of real-world "
                 "accuracy, calibration or fairness.")


@dataclass(frozen=True)
class TrainingConfig:
    random_state: int = 42
    test_size: float = 0.25
    min_rows: int = 50
    min_class_count: int = 10
    regularization_c: float = 1.0
    # Probabilities at which the product flags MEDIUM / HIGH risk (ML_RISK_*_THRESHOLD / 100); reported, not tuned.
    operating_thresholds: tuple[float, ...] = (0.4, 0.7)


@dataclass
class TrainingResult:
    pipeline: Pipeline
    metadata: dict


def build_pipeline(config: TrainingConfig) -> Pipeline:
    numeric = Pipeline([
        # add_indicator keeps "no data recorded" distinguishable from a real value (missing != absent).
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
    ])
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="constant", fill_value="UNKNOWN")),
        ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])
    preprocess = ColumnTransformer(
        [("num", numeric, NUMERIC_FEATURES), ("cat", categorical, CATEGORICAL_FEATURES)],
        remainder="drop",
    )
    classifier = LogisticRegression(
        C=config.regularization_c, class_weight="balanced", max_iter=2000, random_state=config.random_state
    )
    return Pipeline([("preprocess", preprocess), ("classifier", classifier)])


def dataset_fingerprint(frame: pd.DataFrame) -> str:
    """SHA-256 of the validated data in canonical form (sorted columns and rows)."""
    canonical = frame[sorted(frame.columns)].sort_values(sorted(frame.columns)).to_csv(index=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare_dataset(raw: pd.DataFrame, config: TrainingConfig) -> tuple[pd.DataFrame, int]:
    data = validate_frame(raw, require_target=True)
    before = len(data)
    data = data.drop_duplicates().reset_index(drop=True)
    duplicates = before - len(data)

    errors = []
    if len(data) < config.min_rows:
        errors.append(f"At least {config.min_rows} distinct rows are required (got {len(data)})")
    counts = data[TARGET].value_counts()
    if len(counts) < 2:
        errors.append("Both classes are required (attrited = 0 and 1)")
    elif counts.min() < config.min_class_count:
        errors.append(f"Each class needs at least {config.min_class_count} rows (minority has {int(counts.min())})")
    if errors:
        raise FeatureValidationError(errors)
    return data, duplicates


def _at_threshold(y_true, y_prob, threshold: float) -> dict:
    y_pred = (y_prob >= threshold).astype(int)
    return {
        "threshold": threshold,
        "flagged_rate": round(float(y_pred.mean()), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
    }


def _calibration(y_true, y_prob) -> list[dict]:
    """Reliability table: mean estimated probability vs. observed rate per equal-width bin. class_weight="balanced"
    shifts probabilities upward for the minority class, so estimates are not expected to be calibrated."""
    edges = np.linspace(0.0, 1.0, CALIBRATION_BINS + 1)
    table = []
    for i, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        in_bin = (y_prob >= low) & ((y_prob < high) if i < CALIBRATION_BINS - 1 else (y_prob <= high))
        count = int(in_bin.sum())
        table.append({
            "range": [round(float(low), 2), round(float(high), 2)],
            "count": count,
            "mean_estimate": round(float(y_prob[in_bin].mean()), 4) if count else None,
            "observed_rate": round(float(y_true[in_bin].mean()), 4) if count else None,
        })
    return table


def _metrics(y_true, y_prob, operating_thresholds: tuple[float, ...] = ()) -> dict:
    y_pred = (y_prob >= DECISION_THRESHOLD).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    both_classes = len(set(y_true)) == 2
    return {
        "decision_threshold": DECISION_THRESHOLD,
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        # ROC-AUC is undefined unless both classes are present in the evaluation split.
        "roc_auc": round(float(roc_auc_score(y_true, y_prob)), 4) if both_classes else None,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        # Lower is better; 0.25 is what a constant 0.5 estimate scores.
        "brier_score": round(float(brier_score_loss(y_true, y_prob)), 4),
        "at_operating_thresholds": [_at_threshold(y_true, y_prob, t) for t in operating_thresholds],
        "calibration": _calibration(y_true, y_prob),
        "evaluation_rows": int(len(y_true)),
        "scope": METRICS_SCOPE,
    }


def train(raw: pd.DataFrame, config: TrainingConfig, now: datetime) -> TrainingResult:
    data, duplicates = prepare_dataset(raw, config)
    fingerprint = dataset_fingerprint(data)

    x, y = data[FEATURE_COLUMNS], data[TARGET]
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=config.test_size, stratify=y, random_state=config.random_state
    )
    pipeline = build_pipeline(config)
    pipeline.fit(x_train, y_train)  # preprocessing is fitted on the training split only
    probabilities = pipeline.predict_proba(x_test)[:, 1]

    metadata = {
        "model_version": f"lr-{now:%Y%m%d%H%M%S}-{fingerprint[:8]}",
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_columns": FEATURE_COLUMNS,
        "trained_at": now.isoformat(),
        "dataset_fingerprint": fingerprint,
        "rows": {"total": len(data), "train": len(x_train), "test": len(x_test), "duplicates_removed": duplicates},
        "class_balance": {"positive_rate": round(float(y.mean()), 4)},
        "imbalance_handling": "class_weight=balanced",
        "evaluation": "stratified hold-out split",
        "metrics": _metrics(y_test.to_numpy(), probabilities, config.operating_thresholds),
        "config": asdict(config),
        "sklearn_version": sklearn.__version__,
        # Never set automatically: production readiness needs human review of real-data evaluation.
        "production_validated": False,
    }
    return TrainingResult(pipeline=pipeline, metadata=metadata)
