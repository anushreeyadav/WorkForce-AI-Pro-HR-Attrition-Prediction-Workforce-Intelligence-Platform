"""Workforce task rules: visibility, who may do what, assignee eligibility, transitions and versioned history.

Transitions (the single source of truth; clients name an action, never a status):

    action    from                         to            who
    start     OPEN                         IN_PROGRESS   task manager or assignee
    block     OPEN, IN_PROGRESS            BLOCKED       task manager or assignee (structured reason required)
    unblock   BLOCKED                      IN_PROGRESS   task manager or assignee
    complete  OPEN, IN_PROGRESS            COMPLETED     task manager or assignee (optional summary)
    cancel    OPEN, IN_PROGRESS, BLOCKED   CANCELLED     task manager only (reason required)
    reopen    COMPLETED, CANCELLED         OPEN          task manager only

An action whose target equals the current status is a no-op (no event, audit row or notification).

Task managers: ADMIN/HR for every task; MANAGER for tasks of their own department. Visibility adds the assignee
(any role) for their own tasks. Tasks linked to an intervention or recommendation about the viewer are hidden,
like those records themselves.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, exists, false, or_, select, update
from sqlalchemy.orm import Session

from app.models import (
    Employee,
    EmploymentStatus,
    Intervention,
    Recommendation,
    RecommendationScope,
    Task,
    TaskEvent,
    TaskEventType,
    TaskStatus,
    User,
    UserRole,
)
from app.services import interventions as intervention_service
from app.services import recommendations as recommendation_service
from app.services.employee_access import managed_department_id

S = TaskStatus


@dataclass(frozen=True)
class Transition:
    event: TaskEventType
    allowed_from: tuple[TaskStatus, ...]
    target: TaskStatus
    assignee_may: bool


TRANSITIONS: dict[str, Transition] = {
    "start": Transition(TaskEventType.STARTED, (S.OPEN,), S.IN_PROGRESS, True),
    "block": Transition(TaskEventType.BLOCKED, (S.OPEN, S.IN_PROGRESS), S.BLOCKED, True),
    "unblock": Transition(TaskEventType.UNBLOCKED, (S.BLOCKED,), S.IN_PROGRESS, True),
    "complete": Transition(TaskEventType.COMPLETED, (S.OPEN, S.IN_PROGRESS), S.COMPLETED, True),
    "cancel": Transition(TaskEventType.CANCELLED, (S.OPEN, S.IN_PROGRESS, S.BLOCKED), S.CANCELLED, False),
    "reopen": Transition(TaskEventType.REOPENED, (S.COMPLETED, S.CANCELLED), S.OPEN, False),
}
MAX_DUE_DAYS = 366


class TaskError(Exception):
    """A rejected request; status_code/detail map directly to an HTTP error."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class ConcurrentChange(Exception):
    """The task changed after it was loaded; nothing was written."""


@dataclass(frozen=True)
class Viewer:
    user: User
    employee_id: int | None
    department_id: int | None

    @property
    def org_wide(self) -> bool:
        return self.user.role in (UserRole.ADMIN, UserRole.HR)


def viewer(db: Session, user: User) -> Viewer:
    own = db.scalar(select(Employee).where(Employee.user_id == user.id))
    # department_id grants MANAGER scope only, so it follows the same rule as employee_scope (INACTIVE: none).
    return Viewer(user, own.id if own else None, managed_department_id(own))


def not_about_viewer(user_id: int):
    """Exclude tasks whose source intervention/recommendation concerns the viewer."""
    about_intervention = exists(select(Intervention.id).join(Employee, Employee.id == Intervention.employee_id)
                                .where(Intervention.id == Task.intervention_id, Employee.user_id == user_id))
    about_recommendation = exists(select(Recommendation.id).join(Employee, Employee.id == Recommendation.employee_id)
                                  .where(Recommendation.id == Task.recommendation_id, Employee.user_id == user_id))
    return and_(~about_intervention, ~about_recommendation)


def visible(v: Viewer, stmt: Select) -> Select:
    stmt = stmt.where(not_about_viewer(v.user.id))
    if v.org_wide:
        return stmt
    conditions = []
    if v.employee_id is not None:
        conditions.append(Task.assignee_employee_id == v.employee_id)
    if v.user.role == UserRole.MANAGER and v.department_id is not None:
        conditions.append(Task.department_id == v.department_id)
    return stmt.where(or_(*conditions)) if conditions else stmt.where(false())


def can_manage(v: Viewer, task: Task) -> bool:
    if v.org_wide:
        return True
    return v.user.role == UserRole.MANAGER and v.department_id is not None and task.department_id == v.department_id


def is_assignee(v: Viewer, task: Task) -> bool:
    return v.employee_id is not None and task.assignee_employee_id == v.employee_id


def check_due_date(due: date | None, today: date) -> None:
    if due is not None and (due < today or due > today + timedelta(days=MAX_DUE_DAYS)):
        raise TaskError(422, f"due_date must be between today and {MAX_DUE_DAYS} days ahead")


def _source_visible_to(db: Session, user: User, intervention_id: int | None, recommendation_id: int | None) -> bool:
    if intervention_id is not None:
        return db.scalar(intervention_service.visible(db, user, select(Intervention.id))
                         .where(Intervention.id == intervention_id)) is not None
    if recommendation_id is not None:
        return db.scalar(recommendation_service.visible(db, user, select(Recommendation.id))
                         .where(Recommendation.id == recommendation_id)) is not None
    return True


def source_department(db: Session, v: Viewer, intervention_id: int | None, recommendation_id: int | None) -> int | None:
    """Department of a source the actor may see (404 otherwise); None for organization-level recommendations."""
    if intervention_id is not None:
        if not _source_visible_to(db, v.user, intervention_id, None):
            raise TaskError(404, "Intervention not found")
        return db.scalar(select(Employee.department_id).join(Intervention, Intervention.employee_id == Employee.id)
                         .where(Intervention.id == intervention_id))
    if recommendation_id is not None:
        if not _source_visible_to(db, v.user, None, recommendation_id):
            raise TaskError(404, "Recommendation not found")
        rec = db.get(Recommendation, recommendation_id)
        if rec.scope == RecommendationScope.INDIVIDUAL:
            return db.scalar(select(Employee.department_id).where(Employee.id == rec.employee_id))
        return rec.department_id
    return None


def check_assignee(db: Session, v: Viewer, employee_id: int, department_id: int | None,
                   intervention_id: int | None, recommendation_id: int | None) -> Employee:
    """Eligible: an employee the actor may assign (MANAGER: own department, which is also the task's), not
    INACTIVE, with an active login, and, for a task linked to a source, able to see that source."""
    employee = db.get(Employee, employee_id)
    in_scope = employee is not None and (v.org_wide or (v.user.role == UserRole.MANAGER
                                                        and employee.department_id == v.department_id))
    if not in_scope:
        raise TaskError(404, "Employee not found")
    if not v.org_wide and department_id is not None and employee.department_id != department_id:
        raise TaskError(422, "Managers can only assign employees of the task's department")
    if employee.status == EmploymentStatus.INACTIVE:
        raise TaskError(422, "Inactive employees cannot be assigned tasks")
    user = db.get(User, employee.user_id) if employee.user_id is not None else None
    if user is None or not user.is_active:
        raise TaskError(422, "This employee has no active account and cannot be assigned tasks")
    if not _source_visible_to(db, user, intervention_id, recommendation_id):
        raise TaskError(422, "This employee cannot see the linked record, so cannot be assigned this task")
    return employee


def apply_change(db: Session, task: Task, actor: User, now: datetime, event_type: TaskEventType, values: dict[str, Any],
                 *, from_status: TaskStatus | None = None, to_status: TaskStatus | None = None,
                 details: dict[str, Any] | None = None, allowed_from: tuple[TaskStatus, ...] | None = None) -> int:
    """Conditional update on the loaded version, then append the history event. Returns the new version."""
    new_version = task.version + 1
    conditions = [Task.id == task.id, Task.version == task.version]
    if allowed_from is not None:
        conditions.append(Task.status.in_(allowed_from))
    changed = db.execute(update(Task).where(*conditions)
                         .values(**values, version=new_version, updated_by_user_id=actor.id, updated_at=now)
                         .execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        raise ConcurrentChange()
    db.add(TaskEvent(task_id=task.id, version=new_version, event_type=event_type, from_status=from_status,
                     to_status=to_status, actor_user_id=actor.id, details=details, created_at=now))
    db.flush()
    return new_version


def transition_values(action: str, task: Task, now: datetime, payload) -> dict[str, Any]:
    target = TRANSITIONS[action].target
    values: dict[str, Any] = {"status": target}
    if action in ("start", "unblock"):
        values["started_at"] = task.started_at or now
    if action in ("unblock", "complete", "cancel"):
        values.update(block_reason=None, block_details=None)
    if action == "block":
        values.update(block_reason=payload.block_reason, block_details=payload.block_details)
    elif action == "complete":
        values.update(completed_at=now, completion_summary=payload.completion_summary)
    elif action == "cancel":
        values.update(cancelled_at=now, cancellation_reason=payload.cancellation_reason)
    elif action == "reopen":
        values.update(completed_at=None, completion_summary=None, cancelled_at=None, cancellation_reason=None)
    return values
