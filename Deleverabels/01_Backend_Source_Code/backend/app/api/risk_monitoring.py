"""Risk monitoring: bounded runs over persisted predictions, and the HR alert review workflow.

Alerts are review aids, not proof of intent. Nothing here changes employee, attendance, leave or payroll
data, creates predictions or trains models. Each endpoint commits once, together with its audit rows.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.api.workforce_analytics import resolve_department
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    AlertStatus,
    Employee,
    RiskAlert,
    RiskEvent,
    User,
    UserRole,
)
from app.models.base import utcnow
from app.models.risk_monitoring import ACTIVE_ALERT_STATUSES
from app.schemas.common import Page
from app.schemas.risk_monitoring import (
    AcknowledgeRequest,
    AlertListParams,
    AlertRead,
    AssignRequest,
    EscalationEvaluateRequest,
    EscalationEvaluateResult,
    EscalationRead,
    EventRead,
    HistoryParams,
    ResolveRequest,
    RiskSummary,
    RunRequest,
    RunSummary,
)
from app.services import alert_sla
from app.services import attrition as attrition_service
from app.services import risk_monitoring as service
from app.services.audit import record_audit
from app.services.employee_access import apply_scope, employee_scope
from app.services.notifications import notify
from app.services.pagination import paginate
from app.services.search import local_day_bounds

router = APIRouter(prefix="/api/risk-monitoring", tags=["risk-monitoring"])

Runner = Annotated[User, Depends(require_roles(*permissions.RISK_MONITOR_RUN))]
Reviewer = Annotated[User, Depends(require_roles(*permissions.RISK_ALERT_REVIEW))]
Assigner = Annotated[User, Depends(require_roles(*permissions.RISK_ALERT_ASSIGN))]
Escalator = Annotated[User, Depends(require_roles(*permissions.RISK_ESCALATION_EVALUATE))]

ALERT_NOT_FOUND = "Alert not found"
TERMINAL = (AlertStatus.RESOLVED, AlertStatus.DISMISSED)


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _load_visible(db: DbSession, user: User, alert_id: int) -> RiskAlert:
    """404 for missing or out-of-scope alerts (and for alerts about the caller)."""
    alert = db.scalar(
        service.visible_alerts(db, user, select(RiskAlert).options(selectinload(RiskAlert.employee)))
        .where(RiskAlert.id == alert_id).execution_options(populate_existing=True))
    if alert is None:
        raise _error(status.HTTP_404_NOT_FOUND, ALERT_NOT_FOUND)
    return alert


def _reload(db: DbSession, user: User, alert_id: int) -> AlertRead:
    return AlertRead.model_validate(_load_visible(db, user, alert_id))


# --- Monitoring run ---------------------------------------------------------------------------------


@router.post("/run", response_model=RunSummary)
def run_monitoring(payload: RunRequest, db: DbSession, actor: Runner) -> RunSummary:
    """Examine up to `limit` current employees (by id, after the cursor). Safe to repeat: events are
    deduplicated and an employee never gets a second active alert. Uses persisted predictions only."""
    settings = get_settings()
    limit = min(payload.limit, settings.risk_monitoring_max_batch)
    now = utcnow()
    try:
        result = service.run_monitoring(db, settings, now, limit, payload.after_employee_id)
        for alert in result.created_alerts:
            record_audit(db, actor=actor, action="risk.alert_created", entity_type="risk_alert", entity_id=alert.id,
                         details={"employee_id": alert.employee_id, "trigger_type": alert.trigger_type.value,
                                  "priority": alert.priority.value, "model_version": alert.model_version})
        for alert, old, new in result.escalations:
            record_audit(db, actor=actor, action="risk.alert_escalated", entity_type="risk_alert", entity_id=alert.id,
                         details={"from_priority": old.value, "to_priority": new.value})
        record_audit(db, actor=actor, action="risk.monitoring_run", entity_type="risk_monitoring", entity_id="run",
                     details={"examined": result.examined_employees, "new_events": result.new_events,
                              "alerts_created": result.alerts_created, "alerts_updated": result.alerts_updated,
                              "skipped": result.skipped, "stale_predictions": result.stale_predictions,
                              "event_ids": result.event_ids[:1000], "after_employee_id": payload.after_employee_id,
                              "limit": limit})
        db.commit()
    except IntegrityError:
        # A concurrent run recorded the same events first (unique dedup keys); nothing from this run is kept.
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Another monitoring run recorded these events first; retry") from None

    model = attrition_service.active_model(db)
    return RunSummary(examined_employees=result.examined_employees, predictions_considered=result.predictions_considered,
                      new_events=result.new_events, alerts_created=result.alerts_created,
                      alerts_updated=result.alerts_updated, skipped=result.skipped,
                      stale_predictions=result.stale_predictions,
                      next_after_employee_id=result.next_after_employee_id, active_model_available=model is not None)


# --- Alerts -------------------------------------------------------------------------------------


@router.get("/alerts", response_model=Page[AlertRead])
def list_alerts(params: Annotated[AlertListParams, Query()], db: DbSession, user: Reviewer) -> Page[AlertRead]:
    stmt = service.visible_alerts(db, user, select(RiskAlert).options(selectinload(RiskAlert.employee)))
    for value, column in ((params.status, RiskAlert.status), (params.priority, RiskAlert.priority),
                          (params.trigger_type, RiskAlert.trigger_type), (params.department_id, Employee.department_id),
                          (params.employee_id, RiskAlert.employee_id)):
        if value is not None:
            stmt = stmt.where(column == value)
    if params.assigned_to_me:
        stmt = stmt.where(RiskAlert.assigned_to_user_id == user.id)
    lower, upper = local_day_bounds(params.detected_from, params.detected_to, get_settings().org_tz)
    if lower is not None:
        stmt = stmt.where(RiskAlert.first_detected_at >= lower)
    if upper is not None:
        stmt = stmt.where(RiskAlert.first_detected_at < upper)
    stmt = stmt.order_by(RiskAlert.last_observed_at.desc(), RiskAlert.id.desc())
    items, total = paginate(db, stmt, params)
    return Page[AlertRead].build([AlertRead.model_validate(a) for a in items], total, params)


@router.get("/alerts/{alert_id}", response_model=AlertRead)
def get_alert(alert_id: int, db: DbSession, user: Reviewer) -> AlertRead:
    return AlertRead.model_validate(_load_visible(db, user, alert_id))


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertRead)
def acknowledge_alert(alert_id: int, payload: AcknowledgeRequest, db: DbSession, user: Reviewer) -> AlertRead:
    """OPEN → ACKNOWLEDGED. Repeating it on an acknowledged alert is a no-op (200)."""
    alert = _load_visible(db, user, alert_id)
    if alert.status == AlertStatus.ACKNOWLEDGED:
        return AlertRead.model_validate(alert)
    if alert.status in TERMINAL:
        raise _error(status.HTTP_409_CONFLICT, f"Alert is already {alert.status.value.lower()}")
    values = {"status": AlertStatus.ACKNOWLEDGED, "acknowledged_at": utcnow(), "acknowledged_by_user_id": user.id}
    if payload.note:
        values["reviewer_notes"] = payload.note
    if not service.transition(db, alert.id, (AlertStatus.OPEN,), values):
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Alert was changed by another reviewer; reload it")
    record_audit(db, actor=user, action="risk.alert_acknowledged", entity_type="risk_alert", entity_id=alert.id,
                 details={"from_status": "OPEN", "to_status": "ACKNOWLEDGED", "has_note": bool(payload.note)})
    db.commit()
    return _reload(db, user, alert.id)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertRead)
def resolve_alert(alert_id: int, payload: ResolveRequest, db: DbSession, user: Reviewer) -> AlertRead:
    """OPEN/ACKNOWLEDGED → RESOLVED or DISMISSED, with a reason. Repeating the same outcome is a no-op;
    a different outcome on a closed alert is 409. Later qualifying events create a new alert."""
    alert = _load_visible(db, user, alert_id)
    outcome = AlertStatus(payload.outcome)
    if alert.status == outcome:
        return AlertRead.model_validate(alert)
    if alert.status in TERMINAL:
        raise _error(status.HTTP_409_CONFLICT, f"Alert is already {alert.status.value.lower()}")
    previous = alert.status
    values = {"status": outcome, "resolved_at": utcnow(), "resolved_by_user_id": user.id,
              "resolution_reason": payload.reason}
    if payload.note:
        values["reviewer_notes"] = payload.note
    if not service.transition(db, alert.id, ACTIVE_ALERT_STATUSES, values):
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Alert was changed by another reviewer; reload it")
    record_audit(db, actor=user, action=f"risk.alert_{outcome.value.lower()}", entity_type="risk_alert",
                 entity_id=alert.id, details={"from_status": previous.value, "to_status": outcome.value})
    db.commit()
    return _reload(db, user, alert.id)


@router.post("/alerts/{alert_id}/assign", response_model=AlertRead)
def assign_alert(alert_id: int, payload: AssignRequest, db: DbSession, user: Assigner) -> AlertRead:
    """Assign an active alert to an active ADMIN/HR user, or to a MANAGER whose department covers the
    employee. Never to the employee the alert is about."""
    alert = _load_visible(db, user, alert_id)
    if alert.status in TERMINAL:
        raise _error(status.HTTP_409_CONFLICT, f"Alert is already {alert.status.value.lower()}")
    assignee = db.get(User, payload.user_id)
    eligible = (
        assignee is not None and assignee.is_active and assignee.id != alert.employee.user_id
        and (assignee.role in (UserRole.ADMIN, UserRole.HR)
             or (assignee.role == UserRole.MANAGER and service.can_see_employee(db, assignee, alert.employee)))
    )
    if not eligible:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "This user cannot be assigned to this alert")
    if alert.assigned_to_user_id == assignee.id:
        return AlertRead.model_validate(alert)
    if not service.transition(db, alert.id, ACTIVE_ALERT_STATUSES, {"assigned_to_user_id": assignee.id}):
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Alert was changed by another reviewer; reload it")
    record_audit(db, actor=user, action="risk.alert_assigned", entity_type="risk_alert", entity_id=alert.id,
                 details={"assigned_to_user_id": assignee.id, "previous_assignee": alert.assigned_to_user_id})
    if assignee.id != user.id:
        notify(db, user_id=assignee.id, category="risk_alert", title="Risk alert assigned to you",
               message=f"Risk alert #{alert.id} ({alert.priority.value} priority) has been assigned to you.",
               entity_type="risk_alert", entity_id=alert.id)
    db.commit()
    return _reload(db, user, alert.id)


# --- History and summary --------------------------------------------------------------------------


@router.get("/employees/{employee_id}/history", response_model=Page[EventRead])
def employee_history(employee_id: int, params: Annotated[HistoryParams, Query()], db: DbSession, user: Reviewer) -> Page[EventRead]:
    employee = db.get(Employee, employee_id)
    if employee is None or not service.can_see_employee(db, user, employee):
        raise _error(status.HTTP_404_NOT_FOUND, "Employee not found")
    stmt = select(RiskEvent).where(RiskEvent.employee_id == employee.id)
    if params.event_type is not None:
        stmt = stmt.where(RiskEvent.event_type == params.event_type)
    items, total = paginate(db, stmt.order_by(RiskEvent.detected_at.desc(), RiskEvent.id.desc()), params)
    return Page[EventRead].build([EventRead.model_validate(e) for e in items], total, params)


@router.get("/summary", response_model=RiskSummary)
def risk_summary(db: DbSession, user: Reviewer) -> RiskSummary:
    """Counts over the caller's scope only (managers: their department), so nothing leaks across departments."""
    now = utcnow()
    base = service.visible_alerts(db, user, select(RiskAlert))
    alerts = db.execute(base.with_only_columns(RiskAlert.status, RiskAlert.priority, Employee.department_id,
                                               RiskAlert.first_detected_at, RiskAlert.acknowledged_at,
                                               RiskAlert.resolved_at, RiskAlert.created_at)).all()
    policy = alert_sla.SlaPolicy.from_settings(get_settings())
    by_status = {s.value: 0 for s in AlertStatus}
    by_priority = {p: 0 for p in ("LOW", "MEDIUM", "HIGH")}
    by_department: dict[str, int] = {}
    by_sla = {s: 0 for s in alert_sla.SLA_STATES if s != alert_sla.CLOSED}
    for alert_status, priority, department_id, first_detected, acknowledged, resolved, _created in alerts:
        by_status[alert_status.value] += 1
        if alert_status in ACTIVE_ALERT_STATUSES:
            by_priority[priority.value] += 1
            by_department[str(department_id)] = by_department.get(str(department_id), 0) + 1
            by_sla[alert_sla.sla_status(priority, alert_status, first_detected, acknowledged, resolved, now, policy).state] += 1

    scope = employee_scope(db, user)
    events_stmt = (select(RiskEvent.event_type, func.count(RiskEvent.id))
                   .join(Employee, Employee.id == RiskEvent.employee_id)
                   .where(RiskEvent.detected_at >= now - timedelta(days=30))
                   .where((Employee.user_id.is_(None)) | (Employee.user_id != user.id))
                   .group_by(RiskEvent.event_type))
    events = {t.value: n for t, n in db.execute(apply_scope(events_stmt, scope))}
    return RiskSummary(
        alerts_by_status=by_status, active_alerts_by_priority=by_priority, active_alerts_by_department=by_department,
        alerts_created_last_7_days=sum(1 for *_, created in alerts if created >= now - timedelta(days=7)),
        alerts_created_last_30_days=sum(1 for *_, created in alerts if created >= now - timedelta(days=30)),
        events_last_30_days_by_type=events, active_alerts_by_sla_state=by_sla,
    )


# --- Review deadlines and escalation -----------------------------------------------------------------


@router.post("/escalations/evaluate", response_model=EscalationEvaluateResult)
def evaluate_escalations(payload: EscalationEvaluateRequest, db: DbSession, actor: Escalator) -> EscalationEvaluateResult:
    """Explicit, bounded evaluation of review deadlines for active alerts in the caller's scope. Safe to repeat:
    each (alert, stage) is escalated and notified at most once. Never changes alert status, priority or
    assignee, never calls a model, and never creates alerts, interventions or recommendations."""
    settings = get_settings()
    department_id = resolve_department(db, actor, payload.department_id)
    limit = min(payload.limit, settings.alert_escalation_max_batch)
    now = utcnow()
    try:
        result = alert_sla.evaluate(db, settings, actor, now, limit, payload.after_alert_id, department_id)
        for item in result.escalated:
            record_audit(db, actor=actor, action="risk.alert_review_escalated", entity_type="risk_alert",
                         entity_id=item.alert_id,
                         details={"escalation_id": item.escalation_id, "stage": item.stage.value,
                                  "priority": item.priority.value, "review_due_at": item.review_due_at.isoformat(),
                                  "recipient_count": item.recipient_count})
        record_audit(db, actor=actor, action="risk.escalation_evaluated", entity_type="risk_monitoring",
                     entity_id="escalations",
                     details={"examined": result.examined, "by_state": result.by_state,
                              "escalated_alert_ids": [i.alert_id for i in result.escalated][:1000],
                              "already_escalated": result.already_escalated,
                              "no_eligible_recipient": len(result.no_eligible_recipient),
                              "notifications_created": result.notifications_created,
                              "department_id": department_id, "after_alert_id": payload.after_alert_id, "limit": limit})
        db.commit()
    except IntegrityError:
        # A concurrent evaluation recorded the same (alert, stage) first; nothing from this one is kept.
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Another evaluation escalated these alerts first; retry") from None
    return EscalationEvaluateResult(
        examined_alerts=result.examined, on_track=result.by_state[alert_sla.ON_TRACK],
        approaching=result.by_state[alert_sla.APPROACHING], overdue=result.by_state[alert_sla.OVERDUE],
        acknowledged=result.by_state[alert_sla.ACKNOWLEDGED],
        new_escalations=[EscalationRead(alert_id=i.alert_id, stage=i.stage, priority=i.priority,
                                        review_due_at=i.review_due_at, recipient_count=i.recipient_count)
                         for i in result.escalated],
        already_escalated=result.already_escalated, approaching_not_notified=result.approaching_not_notified,
        no_eligible_recipient_alert_ids=result.no_eligible_recipient,
        notifications_created=result.notifications_created, next_after_alert_id=result.next_after_alert_id)
