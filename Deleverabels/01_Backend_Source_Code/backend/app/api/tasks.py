"""Workforce task management: create, assign, track and complete HR follow-up tasks.

- Every endpoint commits once: the change, its history event, its audit row and its notifications succeed or
  fail together. Repeated requests that change nothing write nothing.
- Audit rows and notifications carry ids, codes and statuses only, never titles, descriptions, summaries,
  block details or cancellation reasons.
- Out-of-scope tasks, employees and sources return 404, so their existence is not revealed.
- Completing a task linked to an intervention or recommendation does not change that record.
"""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    Department,
    Employee,
    Task,
    TaskEvent,
    TaskEventType,
    TaskPriority,
    TaskStatus,
    TaskType,
    User,
)
from app.models.base import utcnow
from app.models.task import ACTIVE_TASK_STATUSES
from app.schemas.common import Page, PaginationParams
from app.schemas.task import (
    TaskAssign,
    TaskCreate,
    TaskDetail,
    TaskEventRead,
    TaskListParams,
    TaskRead,
    TaskSummary,
    TaskTransition,
    TaskUpdate,
)
from app.services import tasks as service
from app.services.audit import record_audit
from app.services.search import keyword_condition
from app.services.notifications import department_manager_user_ids, notify
from app.services.pagination import paginate

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

Reader = Annotated[User, Depends(require_roles(*permissions.TASK_READ))]
Manager = Annotated[User, Depends(require_roles(*permissions.TASK_MANAGE))]

NOT_FOUND = "Task not found"
CONCURRENT = "The task was changed by someone else; reload it and try again"
PRIORITY_ORDER = {TaskPriority.CRITICAL: 0, TaskPriority.HIGH: 1, TaskPriority.MEDIUM: 2, TaskPriority.LOW: 3}


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _today() -> date:
    return utcnow().astimezone(get_settings().org_tz).date()


def _read(task: Task, v: service.Viewer, model=TaskRead, **extra):
    overdue = task.status in ACTIVE_TASK_STATUSES and task.due_date is not None and task.due_date < _today()
    return model.model_validate(task).model_copy(update={"is_overdue": overdue, "can_manage": service.can_manage(v, task),
                                                         **extra})


def _load(db: DbSession, v: service.Viewer, task_id: int) -> Task:
    task = db.scalar(service.visible(v, select(Task).options(selectinload(Task.assignee)))
                     .where(Task.id == task_id).execution_options(populate_existing=True))
    if task is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return task


def _require_manager(v: service.Viewer, task: Task) -> None:
    if not service.can_manage(v, task):
        raise _error(status.HTTP_403_FORBIDDEN, "Only the task's managers can do this")


def _check_version(task: Task, expected: int | None) -> None:
    if expected is not None and expected != task.version:
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT)


def _assignee_user_id(db: DbSession, task: Task, employee_id: int | None = None) -> int | None:
    target = employee_id if employee_id is not None else task.assignee_employee_id
    return db.scalar(select(Employee.user_id).where(Employee.id == target)) if target is not None else None


def _notify(db: DbSession, user_ids, actor: User, title: str, message: str, task_id: int) -> None:
    """One notification per distinct recipient, never to the actor."""
    for user_id in sorted({u for u in user_ids if u is not None and u != actor.id}):
        notify(db, user_id=user_id, category="task", title=title, message=message, entity_type="task", entity_id=task_id)


def _apply(db: DbSession, task: Task, actor: User, event_type: TaskEventType, values: dict, audit_details: dict,
           **kwargs) -> None:
    try:
        service.apply_change(db, task, actor, utcnow(), event_type, values, **kwargs)
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT) from None
    record_audit(db, actor=actor, action=f"task.{event_type.value.lower()}", entity_type="task", entity_id=task.id,
                 details=audit_details)


def _service_call(db: DbSession, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.TaskError as exc:
        db.rollback()
        raise _error(exc.status_code, exc.detail) from None


# --- Static routes (declared before /{task_id}) ---------------------------------------------------------


@router.get("/summary", response_model=TaskSummary)
def task_summary(db: DbSession, user: Reader) -> TaskSummary:
    """Counts over the tasks you can see (same scope as the list)."""
    v = service.viewer(db, user)
    rows = db.execute(service.visible(v, select(Task.status, Task.priority, Task.task_type, Task.due_date,
                                                Task.assignee_employee_id))).all()
    today = _today()
    by_status = {s.value: 0 for s in TaskStatus}
    by_priority = {p.value: 0 for p in TaskPriority}
    by_type = {t.value: 0 for t in TaskType}
    overdue = soon = mine = unassigned = 0
    for task_status, priority, task_type, due, assignee in rows:
        by_status[task_status.value] += 1
        if task_status not in ACTIVE_TASK_STATUSES:
            continue
        by_priority[priority.value] += 1
        by_type[task_type.value] += 1
        overdue += int(due is not None and due < today)
        soon += int(due is not None and today <= due <= today + timedelta(days=7))
        mine += int(v.employee_id is not None and assignee == v.employee_id)
        unassigned += int(assignee is None)
    return TaskSummary(by_status=by_status, active_by_priority=by_priority, active_by_type=by_type, overdue=overdue,
                       due_within_7_days=soon, assigned_to_me_active=mine, unassigned_active=unassigned)


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, response: Response, db: DbSession, actor: Manager) -> TaskRead:
    v = service.viewer(db, actor)
    if payload.idempotency_key:
        existing = db.scalar(select(Task).where(Task.idempotency_key == payload.idempotency_key))
        if existing is not None:
            if existing.created_by_user_id != actor.id:
                raise _error(status.HTTP_409_CONFLICT, "This idempotency_key has already been used")
            response.status_code = status.HTTP_200_OK  # retry: same task, no new event or notification
            return _read(_load(db, v, existing.id), v)

    _service_call(db, service.check_due_date, payload.due_date, _today())
    source_dept = _service_call(db, service.source_department, db, v, payload.intervention_id, payload.recommendation_id)
    has_source = payload.intervention_id is not None or payload.recommendation_id is not None
    department_id = payload.department_id if payload.department_id is not None else source_dept
    if has_source and payload.department_id is not None and payload.department_id != source_dept:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "department_id must match the linked record's department")
    if not v.org_wide:
        if v.department_id is None:
            raise _error(status.HTTP_403_FORBIDDEN, "Your account is not linked to a department")
        if department_id is None:
            department_id = v.department_id
        if department_id != v.department_id:
            raise _error(status.HTTP_404_NOT_FOUND, "Department not found")
    elif payload.department_id is not None and db.get(Department, payload.department_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Department not found")
    assignee = None
    if payload.assignee_employee_id is not None:
        assignee = _service_call(db, service.check_assignee, db, v, payload.assignee_employee_id, department_id,
                                 payload.intervention_id, payload.recommendation_id)
        if department_id is None and not has_source:
            department_id = assignee.department_id

    now = utcnow()
    task = Task(title=payload.title, description=payload.description, task_type=payload.task_type,
                priority=payload.priority, status=TaskStatus.OPEN, due_date=payload.due_date,
                assignee_employee_id=payload.assignee_employee_id, department_id=department_id,
                intervention_id=payload.intervention_id, recommendation_id=payload.recommendation_id,
                idempotency_key=payload.idempotency_key, created_by_user_id=actor.id, updated_by_user_id=actor.id,
                version=1)
    db.add(task)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "This idempotency_key has already been used") from None
    links = {"assignee_employee_id": payload.assignee_employee_id, "department_id": department_id,
             "intervention_id": payload.intervention_id, "recommendation_id": payload.recommendation_id}
    db.add(TaskEvent(task_id=task.id, version=1, event_type=TaskEventType.CREATED, to_status=TaskStatus.OPEN,
                     actor_user_id=actor.id, details=links, created_at=now))
    record_audit(db, actor=actor, action="task.created", entity_type="task", entity_id=task.id,
                 details={**links, "task_type": payload.task_type.value, "priority": payload.priority.value})
    if assignee is not None:
        _notify(db, [assignee.user_id], actor, "Task assigned to you",
                f"Task #{task.id} ({payload.priority.value} priority) has been assigned to you.", task.id)
    db.commit()
    return _read(_load(db, v, task.id), v)


@router.get("", response_model=Page[TaskRead])
def list_tasks(params: Annotated[TaskListParams, Query()], db: DbSession, user: Reader) -> Page[TaskRead]:
    v = service.viewer(db, user)
    stmt = service.visible(v, select(Task).options(selectinload(Task.assignee)))
    for value, column in ((params.status, Task.status), (params.priority, Task.priority), (params.task_type, Task.task_type),
                          (params.assignee_employee_id, Task.assignee_employee_id),
                          (params.created_by_user_id, Task.created_by_user_id), (params.department_id, Task.department_id),
                          (params.intervention_id, Task.intervention_id), (params.recommendation_id, Task.recommendation_id)):
        if value is not None:
            stmt = stmt.where(column == value)
    if params.assigned_to_me:
        stmt = stmt.where(Task.assignee_employee_id == v.employee_id) if v.employee_id else stmt.where(Task.id.is_(None))
    if params.q:
        stmt = stmt.where(keyword_condition(params.q, Task.title))
    if params.due_from:
        stmt = stmt.where(Task.due_date >= params.due_from)
    if params.due_to:
        stmt = stmt.where(Task.due_date <= params.due_to)
    if params.overdue is not None:
        overdue = Task.status.in_(ACTIVE_TASK_STATUSES) & Task.due_date.is_not(None) & (Task.due_date < _today())
        stmt = stmt.where(overdue if params.overdue else ~overdue)
    order = {
        "updated": (Task.updated_at.desc(), Task.id.desc()),
        "created": (Task.created_at.desc(), Task.id.desc()),
        "due": (Task.due_date.is_(None), Task.due_date.asc(), Task.id.asc()),
        "priority": (case({p: rank for p, rank in PRIORITY_ORDER.items()}, value=Task.priority), Task.id.asc()),
    }[params.sort]
    items, total = paginate(db, stmt.order_by(*order), params)
    return Page[TaskRead].build([_read(t, v) for t in items], total, params)


# --- Single task ------------------------------------------------------------------------------------------


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: int, db: DbSession, user: Reader) -> TaskDetail:
    v = service.viewer(db, user)
    task = _load(db, v, task_id)
    events = db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id)
                        .order_by(TaskEvent.version.desc(), TaskEvent.id.desc()).limit(20)).all()
    return _read(task, v, TaskDetail, recent_history=[TaskEventRead.model_validate(e) for e in events])


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: int, payload: TaskUpdate, db: DbSession, user: Reader) -> TaskRead:
    v = service.viewer(db, user)
    task = _load(db, v, task_id)
    _require_manager(v, task)
    if task.status not in ACTIVE_TASK_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, f"A {task.status.value} task cannot be edited; reopen it first")
    _check_version(task, payload.expected_version)
    changes = {k: val for k, val in payload.model_dump(exclude_unset=True, exclude={"expected_version"}).items()
               if getattr(task, k) != val}
    if not changes:
        return _read(task, v)
    if "due_date" in changes:
        _service_call(db, service.check_due_date, changes["due_date"], _today())
    fields = sorted(changes)
    _apply(db, task, user, TaskEventType.UPDATED, changes, {"fields": fields}, details={"fields": fields},
           allowed_from=ACTIVE_TASK_STATUSES)
    if "due_date" in changes:
        _notify(db, [_assignee_user_id(db, task)], user, "Task due date changed",
                f"The due date of task #{task.id} has changed.", task.id)
    db.commit()
    return _read(_load(db, v, task.id), v)


@router.post("/{task_id}/assign", response_model=TaskRead)
def assign_task(task_id: int, payload: TaskAssign, db: DbSession, user: Reader) -> TaskRead:
    v = service.viewer(db, user)
    task = _load(db, v, task_id)
    _require_manager(v, task)
    if task.status not in ACTIVE_TASK_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, f"A {task.status.value} task cannot be reassigned")
    assignee = _service_call(db, service.check_assignee, db, v, payload.assignee_employee_id, task.department_id,
                             task.intervention_id, task.recommendation_id)
    if task.assignee_employee_id == assignee.id:
        return _read(task, v)  # repeat request: no event, no notification
    _check_version(task, payload.expected_version)
    details = {"from_employee_id": task.assignee_employee_id, "to_employee_id": assignee.id}
    _apply(db, task, user, TaskEventType.ASSIGNED, {"assignee_employee_id": assignee.id}, details, details=details,
           allowed_from=ACTIVE_TASK_STATUSES)
    _notify(db, [assignee.user_id], user, "Task assigned to you",
            f"Task #{task.id} ({task.priority.value} priority) has been assigned to you.", task.id)
    db.commit()
    return _read(_load(db, v, task.id), v)


@router.post("/{task_id}/transition", response_model=TaskRead)
def transition_task(task_id: int, payload: TaskTransition, db: DbSession, user: Reader) -> TaskRead:
    v = service.viewer(db, user)
    task = _load(db, v, task_id)
    rule = service.TRANSITIONS[payload.action]
    manager = service.can_manage(v, task)
    if not manager and not (rule.assignee_may and service.is_assignee(v, task)):
        raise _error(status.HTTP_403_FORBIDDEN, f"You are not allowed to {payload.action} this task")
    if task.status == rule.target:
        return _read(task, v)  # idempotent repeat: nothing written
    if task.status not in rule.allowed_from:
        raise _error(status.HTTP_409_CONFLICT, f"Cannot {payload.action} a {task.status.value} task")
    _check_version(task, payload.expected_version)
    previous = task.status
    event_details = {"block_reason": payload.block_reason.value} if payload.block_reason else None
    _apply(db, task, user, rule.event, service.transition_values(payload.action, task, utcnow(), payload),
           {"from_status": previous.value, "to_status": rule.target.value, **(event_details or {})},
           from_status=previous, to_status=rule.target, details=event_details, allowed_from=rule.allowed_from)
    creator, assignee_user = task.created_by_user_id, _assignee_user_id(db, task)
    message = f"Task #{task.id} ({task.priority.value} priority) is now {rule.target.value}."
    if payload.action == "block":
        recipients = [creator] if creator is not None else (
            department_manager_user_ids(db, task.department_id) if task.department_id is not None else [])
        _notify(db, recipients, user, "Task blocked", message, task.id)
    elif payload.action == "complete":
        _notify(db, [creator], user, "Task completed", message, task.id)
    elif payload.action in ("cancel", "reopen"):
        _notify(db, [assignee_user], user, f"Task {'cancelled' if payload.action == 'cancel' else 'reopened'}",
                message, task.id)
    db.commit()
    return _read(_load(db, v, task.id), v)


@router.get("/{task_id}/history", response_model=Page[TaskEventRead])
def task_history(task_id: int, params: Annotated[PaginationParams, Query()], db: DbSession, user: Reader) -> Page[TaskEventRead]:
    v = service.viewer(db, user)
    task = _load(db, v, task_id)
    stmt = select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.version.asc(), TaskEvent.id.asc())
    items, total = paginate(db, stmt, params)
    return Page[TaskEventRead].build([TaskEventRead.model_validate(e) for e in items], total, params)
