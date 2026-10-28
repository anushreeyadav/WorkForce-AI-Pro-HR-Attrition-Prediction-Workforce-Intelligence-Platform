"""Risk monitoring over persisted attrition predictions. It never scores, trains or calls a model.

Rules (documented in README):
- Latest prediction = newest by (predicted_at, id). It must come from a model trained on the current
  feature schema and not REJECTED; otherwise the employee is skipped ("incompatible").
- Previous comparable prediction = newest earlier prediction (strictly earlier predicted_at) from the SAME
  model version. Different model versions are not compared (scores are not calibrated alike).
- THRESHOLD_CROSSED: latest score >= RISK_ALERT_SCORE_THRESHOLD and (no comparable previous prediction or
  previous score below it).
- SCORE_INCREASE / SCORE_DECREASE: |latest - previous| >= RISK_CHANGE_THRESHOLD_POINTS.
- STALE_PREDICTION: latest prediction older than RISK_STALE_PREDICTION_DAYS.
- Events are deduplicated by a key built from prediction ids, so reruns never duplicate them.
- Alerts: raised by THRESHOLD_CROSSED, or by SCORE_INCREASE ending at/above the medium risk threshold.
  At most one active alert per employee; later qualifying events attach to it (priority may escalate,
  never automatically lowered). Decreases and stale predictions are recorded as events only.
- Priority uses the latest score only: HIGH >= ML_RISK_HIGH_THRESHOLD, MEDIUM >= ML_RISK_MEDIUM_THRESHOLD,
  else LOW. No personal attributes are used.
- Employees without predictions are skipped and counted; a missing prediction is never a score.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    AlertPriority,
    AlertStatus,
    AttritionModelVersion,
    AttritionPrediction,
    Employee,
    EmploymentStatus,
    ModelVersionStatus,
    RiskAlert,
    RiskEvent,
    RiskEventType,
    User,
    UserRole,
)
from app.models.risk_monitoring import ACTIVE_ALERT_STATUSES
from app.services.employee_access import apply_scope, employee_scope
from app.services.notifications import notify
from ml.attrition.features import FEATURE_SCHEMA_VERSION

ALERTING_TYPES = (RiskEventType.THRESHOLD_CROSSED, RiskEventType.SCORE_INCREASE)
_PRIORITY_RANK = {AlertPriority.LOW: 0, AlertPriority.MEDIUM: 1, AlertPriority.HIGH: 2}


@dataclass(frozen=True)
class MonitoringConfig:
    alert_threshold: Decimal
    change_points: Decimal
    stale_after: timedelta
    medium: Decimal
    high: Decimal

    @classmethod
    def from_settings(cls, settings: Settings) -> "MonitoringConfig":
        return cls(alert_threshold=Decimal(str(settings.risk_alert_score_threshold)),
                   change_points=Decimal(str(settings.risk_change_threshold_points)),
                   stale_after=timedelta(days=settings.risk_stale_prediction_days),
                   medium=Decimal(str(settings.ml_risk_medium_threshold)),
                   high=Decimal(str(settings.ml_risk_high_threshold)))


def priority_for(score: Decimal, config: MonitoringConfig) -> AlertPriority:
    if score >= config.high:
        return AlertPriority.HIGH
    if score >= config.medium:
        return AlertPriority.MEDIUM
    return AlertPriority.LOW


# --- Pure detection ---------------------------------------------------------------------------------


def latest_and_previous(predictions: list[AttritionPrediction]) -> tuple[AttritionPrediction | None, AttritionPrediction | None]:
    """Latest by (predicted_at, id); previous = newest strictly-earlier prediction of the same model version."""
    if not predictions:
        return None, None
    ordered = sorted(predictions, key=lambda p: (p.predicted_at, p.id))
    latest = ordered[-1]
    for candidate in reversed(ordered[:-1]):
        if candidate.model_version_id == latest.model_version_id and candidate.predicted_at < latest.predicted_at:
            return latest, candidate
    return latest, None


@dataclass(frozen=True)
class Detected:
    event_type: RiskEventType
    dedup_key: str
    current: AttritionPrediction
    previous: AttritionPrediction | None


def detect(latest: AttritionPrediction, previous: AttritionPrediction | None, now: datetime,
           config: MonitoringConfig) -> list[Detected]:
    found = []
    score = latest.risk_score
    if score >= config.alert_threshold and (previous is None or previous.risk_score < config.alert_threshold):
        found.append(Detected(RiskEventType.THRESHOLD_CROSSED, f"THRESHOLD_CROSSED:{latest.id}", latest, previous))
    if previous is not None:
        delta = score - previous.risk_score
        if abs(delta) >= config.change_points:
            kind = RiskEventType.SCORE_INCREASE if delta > 0 else RiskEventType.SCORE_DECREASE
            found.append(Detected(kind, f"{kind.value}:{previous.id}:{latest.id}", latest, previous))
    if now - latest.predicted_at > config.stale_after:
        found.append(Detected(RiskEventType.STALE_PREDICTION, f"STALE_PREDICTION:{latest.id}", latest, previous))
    return found


def raises_alert(event: Detected, config: MonitoringConfig) -> bool:
    if event.event_type == RiskEventType.THRESHOLD_CROSSED:
        return True
    return event.event_type == RiskEventType.SCORE_INCREASE and event.current.risk_score >= config.medium


# --- Monitoring run ----------------------------------------------------------------------------------


@dataclass
class RunResult:
    examined_employees: int = 0
    predictions_considered: int = 0
    new_events: int = 0
    alerts_created: int = 0
    alerts_updated: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: {"no_prediction": 0, "incompatible_model": 0})
    stale_predictions: int = 0
    next_after_employee_id: int | None = None
    event_ids: list[int] = field(default_factory=list)
    created_alerts: list[RiskAlert] = field(default_factory=list)
    escalations: list[tuple[RiskAlert, AlertPriority, AlertPriority]] = field(default_factory=list)


def hr_recipients(db: Session, exclude_user_id: int | None) -> list[int]:
    stmt = select(User.id).where(User.role == UserRole.HR, User.is_active.is_(True))
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return list(db.scalars(stmt.order_by(User.id)))


def active_alerts(db: Session, employee_ids: list[int]) -> dict[int, RiskAlert]:
    return {a.employee_id: a for a in db.scalars(
        select(RiskAlert).where(RiskAlert.employee_id.in_(employee_ids), RiskAlert.status.in_(ACTIVE_ALERT_STATUSES)))}


def existing_event_keys(db: Session, keys: list[str]) -> set[str]:
    """Pre-check for already-recorded events. The unique dedup_key constraint is the final guard if a
    concurrent run records the same event between this check and commit."""
    return set(db.scalars(select(RiskEvent.dedup_key).where(RiskEvent.dedup_key.in_(keys)))) if keys else set()


def run_monitoring(db: Session, settings: Settings, now: datetime, limit: int, after_employee_id: int) -> RunResult:
    config = MonitoringConfig.from_settings(settings)
    result = RunResult()

    employees = list(db.scalars(
        select(Employee).where(Employee.id > after_employee_id, Employee.status != EmploymentStatus.INACTIVE)
        .order_by(Employee.id).limit(limit + 1)))
    if len(employees) > limit:
        employees = employees[:limit]
        result.next_after_employee_id = employees[-1].id
    if not employees:
        return result
    ids = [e.id for e in employees]

    by_employee: dict[int, list[AttritionPrediction]] = {i: [] for i in ids}
    models: dict[int, AttritionModelVersion] = {}
    for prediction, model in db.execute(
        select(AttritionPrediction, AttritionModelVersion)
        .join(AttritionModelVersion, AttritionModelVersion.id == AttritionPrediction.model_version_id)
        .where(AttritionPrediction.employee_id.in_(ids))
    ):
        by_employee[prediction.employee_id].append(prediction)
        models[model.id] = model
    active = active_alerts(db, ids)

    plans: list[tuple[Employee, AttritionPrediction, list[Detected]]] = []
    for employee in employees:
        result.examined_employees += 1
        predictions = by_employee[employee.id]
        result.predictions_considered += len(predictions)
        latest, previous = latest_and_previous(predictions)
        if latest is None:
            result.skipped["no_prediction"] += 1
            continue
        model = models[latest.model_version_id]
        if (latest.feature_schema_version != FEATURE_SCHEMA_VERSION or model.feature_schema_version != FEATURE_SCHEMA_VERSION
                or model.status == ModelVersionStatus.REJECTED):
            result.skipped["incompatible_model"] += 1
            continue
        plans.append((employee, latest, detect(latest, previous, now, config)))

    existing = existing_event_keys(db, [d.dedup_key for _, _, detected in plans for d in detected])
    hr_users = hr_recipients(db, None)  # read once per run; the employee concerned is excluded per alert below

    for employee, latest, detected in plans:
        new = [d for d in detected if d.dedup_key not in existing]
        result.stale_predictions += any(d.event_type == RiskEventType.STALE_PREDICTION for d in detected)
        alerting = [d for d in new if raises_alert(d, config)]
        alert = active.get(employee.id)
        model_version = models[latest.model_version_id].model_version

        if alerting and alert is None:
            alert = RiskAlert(employee_id=employee.id, trigger_type=alerting[0].event_type,
                              priority=priority_for(latest.risk_score, config), status=AlertStatus.OPEN,
                              latest_prediction_id=latest.id, latest_score=latest.risk_score,
                              latest_category=latest.risk_category, model_version=model_version,
                              event_count=len(alerting), first_detected_at=now, last_observed_at=now)
            db.add(alert)
            db.flush()
            result.alerts_created += 1
            result.created_alerts.append(alert)
            for recipient in (u for u in hr_users if u != employee.user_id):
                notify(db, user_id=recipient, category="risk_alert", title="New attrition-risk alert",
                       message=f"Risk alert #{alert.id} ({alert.priority.value} priority) is ready for review.",
                       entity_type="risk_alert", entity_id=alert.id)
        elif alert is not None and (alerting or latest.id != alert.latest_prediction_id):
            # Same active condition observed again (or a newer prediction exists): update metadata only.
            new_priority = priority_for(latest.risk_score, config)
            if alerting and _PRIORITY_RANK[new_priority] > _PRIORITY_RANK[alert.priority]:
                result.escalations.append((alert, alert.priority, new_priority))
                alert.priority = new_priority
            alert.latest_prediction_id, alert.latest_score = latest.id, latest.risk_score
            alert.latest_category, alert.model_version = latest.risk_category, model_version
            alert.event_count += len(alerting)
            alert.last_observed_at = now
            result.alerts_updated += 1

        for item in new:
            event = RiskEvent(
                employee_id=employee.id, event_type=item.event_type, dedup_key=item.dedup_key,
                current_prediction_id=item.current.id,
                previous_prediction_id=item.previous.id if item.previous else None,
                current_score=item.current.risk_score,
                previous_score=item.previous.risk_score if item.previous else None,
                current_category=item.current.risk_category,
                previous_category=item.previous.risk_category if item.previous else None,
                model_version=model_version, detected_at=now,
                alert_id=alert.id if (alert is not None and item in alerting) else None,
            )
            db.add(event)
            db.flush()
            result.new_events += 1
            result.event_ids.append(event.id)
    return result


# --- Scope and reviewer actions -------------------------------------------------------------------


def visible_alerts(db: Session, user: User, stmt: Select) -> Select:
    """Join employees and apply the caller's scope. Nobody sees alerts about themselves."""
    stmt = stmt.join(Employee, Employee.id == RiskAlert.employee_id)
    stmt = apply_scope(stmt, employee_scope(db, user))
    return stmt.where(or_(Employee.user_id.is_(None), Employee.user_id != user.id))


def can_see_employee(db: Session, user: User, employee: Employee) -> bool:
    return employee.user_id != user.id and employee_scope(db, user).allows(employee)


class TransitionConflict(Exception):
    pass


def transition(db: Session, alert_id: int, allowed_from: tuple[AlertStatus, ...], values: dict) -> bool:
    """Atomic status change; False if the alert is no longer in an allowed state."""
    return db.execute(
        update(RiskAlert).where(RiskAlert.id == alert_id, RiskAlert.status.in_(allowed_from))
        .values(**values).execution_options(synchronize_session=False)
    ).rowcount == 1
