"""Intervention workflow rules: scope, assignee eligibility, transitions and versioned history.

Workflow: OPEN -> IN_PROGRESS -> COMPLETED, and OPEN/IN_PROGRESS -> CANCELLED. COMPLETED and CANCELLED are
final. Every change is a conditional UPDATE on (id, version) followed by an InterventionEvent with the new
version, so concurrent writers cannot silently overwrite each other or record the same event twice.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, or_, select, update
from sqlalchemy.orm import Session

from app.models import (
    Employee,
    Intervention,
    InterventionEvent,
    InterventionEventType,
    InterventionStatus,
    RiskAlert,
    User,
    UserRole,
)
from app.models.intervention import ACTIVE_STATUSES
from app.services import risk_monitoring
from app.services.employee_access import apply_scope, employee_scope

TRANSITIONS = {
    InterventionEventType.STARTED: ((InterventionStatus.OPEN,), InterventionStatus.IN_PROGRESS),
    InterventionEventType.COMPLETED: ((InterventionStatus.IN_PROGRESS,), InterventionStatus.COMPLETED),
    InterventionEventType.CANCELLED: (ACTIVE_STATUSES, InterventionStatus.CANCELLED),
}


class ConcurrentChange(Exception):
    """The intervention changed after it was loaded; nothing was written."""


def visible(db: Session, user: User, stmt: Select) -> Select:
    """Join employees and apply the caller's scope. Nobody sees interventions about themselves."""
    stmt = stmt.join(Employee, Employee.id == Intervention.employee_id)
    stmt = apply_scope(stmt, employee_scope(db, user))
    return stmt.where(or_(Employee.user_id.is_(None), Employee.user_id != user.id))


def can_manage_employee(db: Session, user: User, employee: Employee) -> bool:
    return risk_monitoring.can_see_employee(db, user, employee)


def eligible_assignee(db: Session, assignee: User | None, employee: Employee) -> bool:
    """Active ADMIN/HR, or a MANAGER whose department covers the employee. Never the employee concerned."""
    if assignee is None or not assignee.is_active or assignee.id == employee.user_id:
        return False
    if assignee.role in (UserRole.ADMIN, UserRole.HR):
        return True
    return assignee.role == UserRole.MANAGER and risk_monitoring.can_see_employee(db, assignee, employee)


def visible_alert(db: Session, user: User, alert_id: int) -> RiskAlert | None:
    return db.scalar(risk_monitoring.visible_alerts(db, user, select(RiskAlert)).where(RiskAlert.id == alert_id))


def active_alert_key(alert_id: int | None, category, status: InterventionStatus) -> str | None:
    return f"{alert_id}:{category.value}" if alert_id is not None and status in ACTIVE_STATUSES else None


def apply_change(db: Session, intervention: Intervention, actor: User, now: datetime, event_type: InterventionEventType,
                 values: dict[str, Any], *, from_status: InterventionStatus | None = None,
                 to_status: InterventionStatus | None = None, note: str | None = None,
                 details: dict[str, Any] | None = None, allowed_from: tuple[InterventionStatus, ...] | None = None) -> int:
    """Conditional update on the loaded version, then append the history event. Returns the new version."""
    new_version = intervention.version + 1
    conditions = [Intervention.id == intervention.id, Intervention.version == intervention.version]
    if allowed_from is not None:
        conditions.append(Intervention.status.in_(allowed_from))
    changed = db.execute(
        update(Intervention).where(*conditions)
        .values(**values, version=new_version, updated_by_user_id=actor.id)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise ConcurrentChange()
    db.add(InterventionEvent(intervention_id=intervention.id, version=new_version, event_type=event_type,
                             from_status=from_status, to_status=to_status, actor_user_id=actor.id, note=note,
                             details=details, created_at=now))
    db.flush()
    return new_version


class InterventionError(Exception):
    """A rejected create request; status_code/detail map directly to an HTTP error."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


DUPLICATE_ACTIVE = "An active intervention for this risk alert and category already exists"
MAX_DUE_DAYS = 366


def create(db: Session, actor: User, now: datetime, today, *, employee_id: int, category, title: str,
           description: str | None, priority, due_date, assigned_to_user_id: int | None, risk_alert_id: int | None,
           idempotency_key: str | None, audit, notifier) -> Intervention:
    """Validate scope, alert, assignee and due date, then insert the intervention with its CREATED event,
    audit row and assignee notification (all in the caller's transaction). `audit`/`notifier` are passed
    in by the router so its transaction conventions (and test hooks) apply."""

    employee = db.get(Employee, employee_id)
    if employee is None or not can_manage_employee(db, actor, employee):
        raise InterventionError(404, "Employee not found")
    if risk_alert_id is not None:
        alert = visible_alert(db, actor, risk_alert_id)
        if alert is None:
            raise InterventionError(404, "Risk alert not found")
        if alert.employee_id != employee.id:
            raise InterventionError(422, "The risk alert does not belong to this employee")
    if assigned_to_user_id is not None and not eligible_assignee(db, db.get(User, assigned_to_user_id), employee):
        raise InterventionError(422, "This user cannot be assigned to this intervention")
    if due_date is not None and (due_date < today or due_date > today + timedelta(days=MAX_DUE_DAYS)):
        raise InterventionError(422, f"due_date must be between today and {MAX_DUE_DAYS} days ahead")

    key = active_alert_key(risk_alert_id, category, InterventionStatus.OPEN)
    if key and db.scalar(select(Intervention.id).where(Intervention.active_alert_key == key)):
        raise InterventionError(409, DUPLICATE_ACTIVE)
    intervention = Intervention(
        employee_id=employee.id, risk_alert_id=risk_alert_id, category=category, title=title, description=description,
        priority=priority, due_date=due_date, assigned_to_user_id=assigned_to_user_id, idempotency_key=idempotency_key,
        status=InterventionStatus.OPEN, version=1, active_alert_key=key,
        created_by_user_id=actor.id, updated_by_user_id=actor.id)
    db.add(intervention)
    db.flush()  # IntegrityError (concurrent duplicate) propagates to the caller
    db.add(InterventionEvent(intervention_id=intervention.id, version=1, event_type=InterventionEventType.CREATED,
                             to_status=InterventionStatus.OPEN, actor_user_id=actor.id, created_at=now,
                             details={"assigned_to_user_id": assigned_to_user_id}))
    audit(db, actor=actor, action="intervention.created", entity_type="intervention", entity_id=intervention.id,
          details={"employee_id": employee.id, "risk_alert_id": risk_alert_id, "category": category.value,
                   "priority": priority.value, "assigned_to_user_id": assigned_to_user_id})
    if assigned_to_user_id is not None and assigned_to_user_id != actor.id:
        notifier(db, user_id=assigned_to_user_id, category="intervention", title="Intervention assigned to you",
                 message=f"Intervention #{intervention.id} ({priority.value} priority) has been assigned to you.",
                 entity_type="intervention", entity_id=intervention.id)
    return intervention
