"""Attrition risk: feature extraction from platform data, model registry, training and prediction.

Read-only with respect to employees, attendance, leave and payroll. Writes only model-registry and
prediction rows (plus audit rows via the router), all in the caller's transaction.
"""

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.orm import Session
from sklearn.pipeline import Pipeline

from app.core.config import Settings
from app.models import (
    Attendance,
    AttendanceStatus,
    AttritionModelVersion,
    AttritionPrediction,
    DatasetVersion,
    Employee,
    ModelVersionStatus,
    RiskCategory,
    User,
)
from app.services import dataset_storage
from app.services import datasets as dataset_service
from ml.attrition import artifacts, predictor, training
from ml.attrition.dataset_validation import validate_dataset
from ml.attrition.features import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION

WINDOW_DAYS = 90


class ModelUnavailableError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DatasetError(Exception):
    def __init__(self, status: int, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.status = status
        self.errors = errors


# --- Features ------------------------------------------------------------------------------------


def tenure_months(date_of_joining: date, as_of: date) -> int:
    months = (as_of.year - date_of_joining.year) * 12 + (as_of.month - date_of_joining.month)
    if as_of.day < date_of_joining.day:
        months -= 1
    return min(max(months, 0), 600)


def extract_features(db: Session, employees: list[Employee], as_of: date) -> dict[int, dict]:
    """Schema features for each employee, from attendance in the 90 days ending on as_of.

    One grouped query for all employees. No attendance in the window gives null rates (not zero, and
    never "absent"); recorded_absence_days_90d counts only records explicitly marked ABSENT.
    """
    start = as_of - timedelta(days=WINDOW_DAYS - 1)
    attended = Attendance.status.in_([AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.HALF_DAY])
    rows = db.execute(
        select(
            Attendance.employee_id,
            func.count(Attendance.id),
            func.sum(case((attended, 1), else_=0)),
            func.sum(case((Attendance.status == AttendanceStatus.LATE, 1), else_=0)),
            func.sum(case((Attendance.status == AttendanceStatus.ABSENT, 1), else_=0)),
            func.avg(Attendance.overtime_minutes),
        )
        .where(Attendance.employee_id.in_([e.id for e in employees]),
               Attendance.work_date >= start, Attendance.work_date <= as_of)
        .group_by(Attendance.employee_id)
    ).all()
    stats = {row[0]: row[1:] for row in rows}

    features = {}
    for employee in employees:
        recorded, attended_days, late, absent, avg_overtime = stats.get(employee.id, (0, 0, 0, 0, None))
        # MySQL returns SUM() as Decimal (SQLite as int); features are stored as JSON, so use plain ints and floats.
        attended_days, late = int(attended_days or 0), int(late or 0)
        features[employee.id] = {
            "tenure_months": tenure_months(employee.date_of_joining, as_of),
            "late_rate_90d": round(late / attended_days, 4) if attended_days else None,
            "avg_overtime_minutes_90d": round(float(avg_overtime), 2) if avg_overtime is not None else None,
            "recorded_absence_days_90d": int(absent or 0),
            "attendance_days_recorded_90d": int(recorded or 0),
            "department": str(employee.department_id),
        }
    return features


# --- Model registry and loading ----------------------------------------------------------------

_cache: dict[tuple[str, str], Pipeline] = {}
_cache_lock = threading.Lock()


def thresholds(settings: Settings) -> predictor.Thresholds:
    return predictor.Thresholds(Decimal(str(settings.ml_risk_medium_threshold)),
                                Decimal(str(settings.ml_risk_high_threshold)))


def active_model(db: Session) -> AttritionModelVersion | None:
    return db.scalar(select(AttritionModelVersion).where(AttritionModelVersion.status == ModelVersionStatus.ACTIVE)
                     .order_by(AttritionModelVersion.activated_at.desc()).limit(1))


def load_active(db: Session, settings: Settings) -> tuple[AttritionModelVersion, Pipeline]:
    model = active_model(db)
    if model is None:
        raise ModelUnavailableError("no_active_model", "No attrition model has been trained and activated")
    if model.feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ModelUnavailableError("schema_mismatch", "The active model uses an outdated feature schema")
    key = (model.model_version, model.artifact_sha256)
    with _cache_lock:
        if key not in _cache:
            try:
                _cache[key] = artifacts.load(settings.ml_artifact_dir, model.model_version, model.artifact_sha256,
                                             FEATURE_SCHEMA_VERSION, FEATURE_COLUMNS)
            except artifacts.ArtifactError as exc:
                raise ModelUnavailableError("artifact_error", str(exc)) from None
        return model, _cache[key]


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def predict(db: Session, employee: Employee, features: dict, model: AttritionModelVersion, pipeline: Pipeline,
            settings: Settings, actor: User, now: datetime, batch_id: str | None = None) -> AttritionPrediction:
    outcome = predictor.predict(pipeline, features, thresholds(settings))
    prediction = AttritionPrediction(
        employee_id=employee.id, model_version_id=model.id, feature_schema_version=FEATURE_SCHEMA_VERSION,
        risk_score=outcome.risk_score, risk_category=RiskCategory(outcome.risk_category), features=features,
        explanation={"factors": outcome.factors, "missing_inputs": outcome.missing_inputs},
        predicted_at=now, requested_by_user_id=actor.id, batch_id=batch_id,
    )
    db.add(prediction)
    return prediction


# --- Training and activation --------------------------------------------------------------------


def gate_failures(metadata: dict, settings: Settings) -> list[str]:
    metrics = metadata["metrics"]
    failures = []
    if metrics["roc_auc"] is None:
        failures.append("ROC-AUC could not be computed on the evaluation split")
    elif metrics["roc_auc"] < settings.ml_activation_min_roc_auc:
        failures.append(f"ROC-AUC {metrics['roc_auc']} is below the activation minimum {settings.ml_activation_min_roc_auc}")
    return failures


@dataclass
class TrainingOutcome:
    model: AttritionModelVersion
    gate_failures: list[str]


def train_model(db: Session, settings: Settings, actor: User, now: datetime,
                dataset_version: DatasetVersion) -> TrainingOutcome:
    """Train from a registered, VALIDATED dataset version. Never activates the model: a passing model is
    CANDIDATE and needs an explicit activation; a failing one is REJECTED."""
    try:
        raw = dataset_service.read_frame(settings, dataset_version)  # checksum verified first
    except dataset_storage.DatasetFileError as exc:
        raise DatasetError(409, [exc.message]) from None
    # Revalidate against the current schema; the stored file must still be eligible.
    report = validate_dataset(raw, dataset_service.validation_config(settings), now)
    if not report["training_eligible"]:
        raise DatasetError(409, ["Dataset no longer passes validation against the current feature schema",
                                 *report["fatal_errors"]])
    frame = raw.replace("", np.nan)  # empty cells are missing values, never zeros

    config = training.TrainingConfig(random_state=settings.ml_random_state, min_rows=settings.ml_min_training_rows,
                                     min_class_count=settings.ml_min_class_count,
                                     operating_thresholds=(settings.ml_risk_medium_threshold / 100,
                                                           settings.ml_risk_high_threshold / 100))
    result = training.train(frame, config, now)  # raises FeatureValidationError
    dataset_ref = {"dataset_id": dataset_version.dataset_id, "version": dataset_version.version_number,
                   "sha256": dataset_version.sha256}
    metadata = {**result.metadata, "dataset": dataset_ref}
    failures = gate_failures(metadata, settings)

    Path(settings.ml_artifact_dir).mkdir(parents=True, exist_ok=True)
    sha = artifacts.save(result.pipeline, metadata, Path(settings.ml_artifact_dir))
    model = AttritionModelVersion(
        model_version=metadata["model_version"], feature_schema_version=metadata["feature_schema_version"],
        status=ModelVersionStatus.REJECTED if failures else ModelVersionStatus.CANDIDATE,
        trained_at=now, trained_by_user_id=actor.id, dataset_fingerprint=metadata["dataset_fingerprint"],
        artifact_sha256=sha, training_rows=metadata["rows"]["total"], dataset_version_id=dataset_version.id,
        evaluation={k: metadata[k] for k in ("metrics", "rows", "class_balance", "imbalance_handling",
                                             "evaluation", "config", "sklearn_version", "model_family",
                                             "production_validated", "dataset")},
    )
    db.add(model)
    db.flush()
    return TrainingOutcome(model, failures)


class ActivationError(Exception):
    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or [message]


NOT_A_CANDIDATE = "Only a CANDIDATE model that passed evaluation can be activated"


def activation_failures(model: AttritionModelVersion, settings: Settings) -> list[str]:
    """Checks repeated at activation time, so a working model is never retired for one that cannot serve: the
    feature schema is current, the stored evaluation still meets the CURRENT activation minimum, and the artifact
    passes the same integrity checks as prediction (checksum before unpickling, metadata, pipeline type)."""
    failures = []
    if model.feature_schema_version != FEATURE_SCHEMA_VERSION:
        failures.append("The model was trained on an outdated feature schema")
    failures += gate_failures({"metrics": (model.evaluation or {}).get("metrics", {"roc_auc": None})}, settings)
    try:
        artifacts.load(settings.ml_artifact_dir, model.model_version, model.artifact_sha256, FEATURE_SCHEMA_VERSION,
                       FEATURE_COLUMNS)
    except artifacts.ArtifactError as exc:
        failures.append(f"Artifact check failed: {exc}")
    return failures


def activate_model(db: Session, model: AttritionModelVersion, actor: User, now: datetime,
                   settings: Settings) -> None:
    """Retire the current ACTIVE model and activate this CANDIDATE, atomically within the transaction. Refused
    (ActivationError, nothing changed) unless the candidate passes activation_failures()."""
    if model.status != ModelVersionStatus.CANDIDATE:
        raise ActivationError(NOT_A_CANDIDATE)
    if failures := activation_failures(model, settings):
        raise ActivationError("The model cannot be activated: " + "; ".join(failures), failures)
    db.execute(update(AttritionModelVersion)
               .where(AttritionModelVersion.status == ModelVersionStatus.ACTIVE, AttritionModelVersion.id != model.id)
               .values(status=ModelVersionStatus.RETIRED).execution_options(synchronize_session=False))
    changed = db.execute(
        update(AttritionModelVersion)
        .where(and_(AttritionModelVersion.id == model.id, AttritionModelVersion.status == ModelVersionStatus.CANDIDATE))
        .values(status=ModelVersionStatus.ACTIVE, activated_at=now, activated_by_user_id=actor.id)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise ActivationError(NOT_A_CANDIDATE)
    db.expire(model)
