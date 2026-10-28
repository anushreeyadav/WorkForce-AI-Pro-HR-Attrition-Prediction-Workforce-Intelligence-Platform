"""AI attrition-risk endpoints: HR decision support only.

- No endpoint lets an employee see risk scores (not even their own); ordinary employee endpoints never
  include them.
- Predictions never change employee, attendance, leave or payroll data.
- Without a trained, activated model the API returns 503 instead of inventing scores.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Employee,
    EmploymentStatus,
    ModelVersionStatus,
    RiskCategory,
    User,
)
from app.models.base import utcnow
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.attrition import (
    BatchPredictRequest,
    BatchPredictResult,
    Factor,
    FeatureSnapshot,
    HistoryParams,
    ModelStatus,
    ModelVersionRead,
    PredictionRead,
    RiskSummary,
    SkippedPrediction,
    SummaryParams,
    TrainRequest,
    TrainResult,
)
from app.schemas.common import Page
from app.services import attrition as service
from app.services import datasets as dataset_service
from app.services.audit import record_audit
from app.services.employee_access import employee_scope
from app.services.leave_calendar import org_today
from app.services.pagination import paginate
from ml.attrition.features import FEATURE_SCHEMA_VERSION, FUTURE_FEATURES, FeatureValidationError, describe_schema

router = APIRouter(prefix="/api/ai/attrition", tags=["ai-attrition"])

Predictor = Annotated[User, Depends(require_roles(*permissions.ATTRITION_PREDICT))]
OrgWide = Annotated[User, Depends(require_roles(*permissions.ATTRITION_ORG_WIDE))]
ModelAdmin = Annotated[User, Depends(require_roles(*permissions.ATTRITION_MODEL_ADMIN))]

NOT_FOUND = "Employee not found"
UNAVAILABLE_ACTION = "ai.attrition.prediction_unavailable"


def _error(code: int, detail) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _scoped_employee(db: DbSession, user: User, employee_id: int) -> Employee:
    """404 for missing or out-of-scope employees (managers: own department only); 403 for yourself."""
    employee = db.get(Employee, employee_id)
    if employee is None or not employee_scope(db, user).allows(employee):
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if employee.user_id == user.id:
        raise _error(status.HTTP_403_FORBIDDEN, "You cannot view or generate attrition predictions about yourself")
    return employee


def _load_model_or_503(db: DbSession, user: User):
    settings = get_settings()
    try:
        return service.load_active(db, settings)
    except service.ModelUnavailableError as exc:
        db.rollback()
        # Monitoring: count unavailable attempts (reason code only).
        record_audit(db, actor=user, action=UNAVAILABLE_ACTION, entity_type="attrition_model",
                     entity_id="active", details={"reason": exc.code})
        db.commit()
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE,
                     {"code": exc.code, "message": exc.message,
                      "hint": "An administrator must train and activate a model on a validated dataset."}) from None


def _read(prediction: AttritionPrediction, model_version: str) -> PredictionRead:
    explanation = prediction.explanation or {}
    return PredictionRead(
        id=prediction.id, employee=AttendanceEmployeeRef.model_validate(prediction.employee),
        risk_score=prediction.risk_score, risk_category=prediction.risk_category, model_version=model_version,
        feature_schema_version=prediction.feature_schema_version, predicted_at=prediction.predicted_at,
        features=FeatureSnapshot(**prediction.features),
        factors=[Factor(**f) for f in explanation.get("factors", [])],
        missing_inputs=explanation.get("missing_inputs", []),
    )


def _today():
    return org_today(utcnow(), get_settings().org_tz)


# --- Predictions --------------------------------------------------------------------------------


@router.post("/predict/{employee_id}", response_model=PredictionRead, status_code=status.HTTP_201_CREATED)
def predict_employee(employee_id: int, db: DbSession, user: Predictor) -> PredictionRead:
    employee = _scoped_employee(db, user, employee_id)
    if employee.status == EmploymentStatus.INACTIVE:
        raise _error(status.HTTP_409_CONFLICT, "Predictions are generated only for current employees")
    model, pipeline = _load_model_or_503(db, user)

    features = service.extract_features(db, [employee], _today())[employee.id]
    prediction = service.predict(db, employee, features, model, pipeline, get_settings(), user, utcnow())
    db.flush()
    record_audit(db, actor=user, action="ai.attrition.predicted", entity_type="attrition_prediction",
                 entity_id=prediction.id, details={"employee_id": employee.id, "model_version": model.model_version})
    db.commit()
    return _read(prediction, model.model_version)


@router.post("/predict-batch", response_model=BatchPredictResult)
def predict_batch(payload: BatchPredictRequest, db: DbSession, user: Predictor) -> BatchPredictResult:
    """Up to 100 employees. Out-of-scope or unknown ids are reported identically ("not found")."""
    model, pipeline = _load_model_or_503(db, user)
    scope = employee_scope(db, user)
    found = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_(payload.employee_ids)))}

    eligible, skipped = [], []
    for employee_id in payload.employee_ids:
        employee = found.get(employee_id)
        if employee is None or not scope.allows(employee):
            skipped.append(SkippedPrediction(employee_id=employee_id, reason="Not found"))
        elif employee.user_id == user.id:
            skipped.append(SkippedPrediction(employee_id=employee_id, reason="Cannot predict about yourself"))
        elif employee.status == EmploymentStatus.INACTIVE:
            skipped.append(SkippedPrediction(employee_id=employee_id, reason="Not a current employee"))
        else:
            eligible.append(employee)

    batch_id, now, settings = str(uuid.uuid4()), utcnow(), get_settings()
    features = service.extract_features(db, eligible, _today()) if eligible else {}
    predictions = [service.predict(db, e, features[e.id], model, pipeline, settings, user, now, batch_id) for e in eligible]
    db.flush()
    record_audit(db, actor=user, action="ai.attrition.batch_predicted", entity_type="attrition_batch",
                 entity_id=batch_id, details={"requested": len(payload.employee_ids), "predicted": len(predictions),
                                              "skipped": len(skipped), "model_version": model.model_version})
    db.commit()
    return BatchPredictResult(batch_id=batch_id, model_version=model.model_version,
                              predictions=[_read(p, model.model_version) for p in predictions], skipped=skipped)


@router.get("/history/{employee_id}", response_model=Page[PredictionRead])
def prediction_history(employee_id: int, params: Annotated[HistoryParams, Query()], db: DbSession, user: Predictor) -> Page[PredictionRead]:
    employee = _scoped_employee(db, user, employee_id)
    stmt = (select(AttritionPrediction)
            .options(selectinload(AttritionPrediction.employee), selectinload(AttritionPrediction.model))
            .where(AttritionPrediction.employee_id == employee.id)
            .order_by(AttritionPrediction.predicted_at.desc(), AttritionPrediction.id.desc()))
    items, total = paginate(db, stmt, params)
    return Page[PredictionRead].build([_read(p, p.model.model_version) for p in items], total, params)


@router.get("/summary", response_model=RiskSummary)
def risk_summary(params: Annotated[SummaryParams, Query()], db: DbSession, _: OrgWide) -> RiskSummary:
    latest = (select(func.max(AttritionPrediction.id).label("id"))
              .join(Employee, Employee.id == AttritionPrediction.employee_id)
              .where(Employee.status != EmploymentStatus.INACTIVE)
              .group_by(AttritionPrediction.employee_id))
    if params.department_id is not None:
        latest = latest.where(Employee.department_id == params.department_id)
    rows = db.execute(
        select(AttritionPrediction.risk_category, AttritionModelVersion.model_version, AttritionPrediction.predicted_at)
        .join(AttritionModelVersion, AttritionModelVersion.id == AttritionPrediction.model_version_id)
        .where(AttritionPrediction.id.in_(latest))
    ).all()
    counts = {c.value: 0 for c in RiskCategory}
    for category, _version, _at in rows:
        counts[category.value] += 1
    return RiskSummary(employees_with_predictions=len(rows), by_category=counts,
                       model_versions=sorted({r[1] for r in rows}),
                       latest_prediction_at=max((r[2] for r in rows), default=None))


# --- Model status, training, activation -----------------------------------------------------------


@router.get("/model-status", response_model=ModelStatus)
def model_status(db: DbSession, _: OrgWide) -> ModelStatus:
    settings = get_settings()
    active = service.active_model(db)
    available, reason = False, "No attrition model has been trained and activated"
    if active is not None:
        try:
            service.load_active(db, settings)
            available, reason = True, None
        except service.ModelUnavailableError as exc:
            reason = exc.message

    recent = db.scalars(select(AttritionModelVersion).order_by(AttritionModelVersion.trained_at.desc()).limit(10)).all()
    per_category = {c.value: 0 for c in RiskCategory}
    if active is not None:
        for category, n in db.execute(select(AttritionPrediction.risk_category, func.count(AttritionPrediction.id))
                                      .where(AttritionPrediction.model_version_id == active.id)
                                      .group_by(AttritionPrediction.risk_category)):
            per_category[category.value] = n
    monitoring = {
        "predictions_total": db.scalar(select(func.count(AttritionPrediction.id))) or 0,
        "predictions_by_category_active_model": per_category,
        "unavailable_requests_total": db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == UNAVAILABLE_ACTION)) or 0,
    }
    return ModelStatus(
        available=available, reason=reason,
        active_model=ModelVersionRead.model_validate(active) if active else None,
        recent_models=[ModelVersionRead.model_validate(m) for m in recent],
        feature_schema_version=FEATURE_SCHEMA_VERSION, feature_schema=describe_schema(),
        future_features=FUTURE_FEATURES,
        risk_thresholds={"medium": settings.ml_risk_medium_threshold, "high": settings.ml_risk_high_threshold},
        monitoring=monitoring,
    )


@router.post("/train", response_model=TrainResult, status_code=status.HTTP_201_CREATED)
def train_model(payload: TrainRequest, db: DbSession, actor: ModelAdmin) -> TrainResult:
    """Train from a registered, VALIDATED, training-eligible dataset version (resolved server-side).

    The result is CANDIDATE (passed the evaluation gate) or REJECTED; it is never activated here.
    """
    settings = get_settings()

    def reject(code: int, errors: list[str], reason: str) -> HTTPException:
        db.rollback()
        record_audit(db, actor=actor, action="ai.attrition.training_rejected", entity_type="dataset",
                     entity_id=payload.dataset_id, details={"version": payload.version, "reason": reason})
        db.commit()
        return _error(code, {"errors": errors})

    try:
        dataset_version = dataset_service.resolve_for_training(db, payload.dataset_id, payload.version)
    except dataset_service.DatasetConflictError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.code == "not_found" else status.HTTP_409_CONFLICT
        raise reject(code, [exc.message], exc.code) from None
    try:
        outcome = service.train_model(db, settings, actor, utcnow(), dataset_version)
    except service.DatasetError as exc:
        raise reject(exc.status, exc.errors, "dataset_revalidation_failed") from None
    except FeatureValidationError as exc:
        raise reject(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors, "feature_validation_failed") from None

    model = outcome.model
    dataset_ref = model.evaluation["dataset"]
    record_audit(db, actor=actor, action="ai.attrition.model_trained", entity_type="attrition_model",
                 entity_id=model.model_version,
                 details={"status": model.status.value, "training_rows": model.training_rows,
                          "roc_auc": model.evaluation["metrics"]["roc_auc"], "dataset_id": dataset_ref["dataset_id"],
                          "dataset_version": dataset_ref["version"], "dataset_sha256": dataset_ref["sha256"]})
    db.commit()
    return TrainResult(model=ModelVersionRead.model_validate(model), dataset=dataset_ref,
                       gate_failures=outcome.gate_failures)


@router.post("/models/{model_version}/activate", response_model=ModelVersionRead)
def activate_model(model_version: str, db: DbSession, actor: ModelAdmin) -> AttritionModelVersion:
    model = db.scalar(select(AttritionModelVersion).where(AttritionModelVersion.model_version == model_version))
    if model is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Model version not found")
    try:
        service.activate_model(db, model, actor, utcnow())
    except service.ActivationError as exc:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, str(exc)) from None
    record_audit(db, actor=actor, action="ai.attrition.model_activated", entity_type="attrition_model",
                 entity_id=model_version, details={"status": "ACTIVE"})
    db.commit()
    service.clear_cache()
    db.refresh(model)
    return model
