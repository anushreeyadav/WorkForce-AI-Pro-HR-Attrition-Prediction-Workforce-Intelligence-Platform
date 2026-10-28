"""HR intervention and retention action workflow (internal records).

- Never contacts employees, changes employment status, or touches the linked risk alert.
- Each endpoint commits once with its history event and audit row; any failure rolls everything back.
- Audit rows and notifications carry ids, categories and statuses only, never titles, notes or outcomes.
"""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    Employee,
    Intervention,
    InterventionEvent,
    InterventionEventType,
    InterventionStatus,
    User,
)
from app.models.base import utcnow
from app.models.intervention import ACTIVE_STATUSES
from app.schemas.common import Page, PaginationParams
from app.schemas.intervention import (
    CATEGORY_DESCRIPTIONS,
    GUIDANCE,
    AssignRequest,
    CancelRequest,
    CompleteRequest,
    EventRead,
    InterventionCreate,
    InterventionRead,
    InterventionUpdate,
    ListParams,
    NoteRequest,
    Summary,
    VersionedAction,
)
from app.services import interventions as service
from app.services.audit import record_audit
from app.services.search import keyword_condition
from app.services.notifications import notify
from app.services.pagination import paginate

router = APIRouter(prefix="/api/interventions", tags=["interventions"])

Manager = Annotated[User, Depends(require_roles(*permissions.INTERVENTION_MANAGE))]

NOT_FOUND = "Intervention not found"
DUPLICATE = "An active intervention for this risk alert and category already exists"
CONCURRENT = "The intervention was changed by someone else; reload it and try again"
MAX_DUE_DAYS = 366


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _today() -> date:
    return utcnow().astimezone(get_settings().org_tz).date()


def _read(intervention: Intervention) -> InterventionRead:
    overdue = (intervention.status in ACTIVE_STATUSES and intervention.due_date is not None
               and intervention.due_date < _today())
    return InterventionRead.model_validate(intervention).model_copy(update={"is_overdue": overdue})


def _load(db: DbSession, user: User, intervention_id: int) -> Intervention:
    intervention = db.scalar(
        service.visible(db, user, select(Intervention).options(selectinload(Intervention.employee)))
        .where(Intervention.id == intervention_id).execution_options(populate_existing=True))
    if intervention is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return intervention


def _check_due_date(due: date | None) -> None:
    if due is None:
        return
    today = _today()
    if due < today or due > today + timedelta(days=MAX_DUE_DAYS):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"due_date must be between today and {MAX_DUE_DAYS} days ahead")


def _check_version(intervention: Intervention, expected: int | None) -> None:
    if expected is not None and expected != intervention.version:
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT)


def _notify_assignee(db: DbSession, intervention_id: int, priority: str, assignee_id: int | None, actor: User) -> None:
    if assignee_id is not None and assignee_id != actor.id:
        notify(db, user_id=assignee_id, category="intervention", title="Intervention assigned to you",
               message=f"Intervention #{intervention_id} ({priority} priority) has been assigned to you.",
               entity_type="intervention", entity_id=intervention_id)


def _commit(db: DbSession) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, DUPLICATE) from None


def _mutate(db: DbSession, intervention: Intervention, actor: User, event_type: InterventionEventType, values: dict,
            audit_details: dict, **kwargs) -> None:
    try:
        service.apply_change(db, intervention, actor, utcnow(), event_type, values, **kwargs)
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT) from None
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, DUPLICATE) from None
    record_audit(db, actor=actor, action=f"intervention.{event_type.value.lower()}", entity_type="intervention",
                 entity_id=intervention.id, details=audit_details)


# --- Static routes (declared before /{intervention_id}) ---------------------------------------------


@router.get("/categories")
def list_categories(_: Manager) -> dict:
    """General HR workflow options, not personalised or AI-generated recommendations."""
    return {"note": "General HR workflow options; not personalised or AI-generated recommendations.",
            "guidance": GUIDANCE,
            "categories": [{"category": c.value, "description": d} for c, d in CATEGORY_DESCRIPTIONS.items()]}


@router.get("/summary", response_model=Summary)
def summary(db: DbSession, user: Manager) -> Summary:
    rows = db.execute(service.visible(db, user, select(Intervention)).with_only_columns(
        Intervention.status, Intervention.priority, Intervention.category, Intervention.due_date)).all()
    today = _today()
    by_status = {s.value: 0 for s in InterventionStatus}
    by_priority = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    by_category: dict[str, int] = {}
    overdue = 0
    for row_status, priority, category, due in rows:
        by_status[row_status.value] += 1
        if row_status in ACTIVE_STATUSES:
            by_priority[priority.value] += 1
            by_category[category.value] = by_category.get(category.value, 0) + 1
            overdue += int(due is not None and due < today)
    return Summary(by_status=by_status, active_by_priority=by_priority, active_by_category=by_category, overdue=overdue)


@router.post("", response_model=InterventionRead, status_code=status.HTTP_201_CREATED)
def create_intervention(payload: InterventionCreate, response: Response, db: DbSession, actor: Manager) -> InterventionRead:
    if payload.idempotency_key:
        existing = db.scalar(select(Intervention).where(Intervention.idempotency_key == payload.idempotency_key))
        if existing is not None:
            if existing.created_by_user_id != actor.id:
                raise _error(status.HTTP_409_CONFLICT, "This idempotency_key has already been used")
            response.status_code = status.HTTP_200_OK  # retry: same record, no new event or notification
            return _read(_load(db, actor, existing.id))

    try:
        intervention = service.create(
            db, actor, utcnow(), _today(), **payload.model_dump(exclude={"idempotency_key"}),
            idempotency_key=payload.idempotency_key, audit=record_audit, notifier=notify)
    except service.InterventionError as exc:
        db.rollback()
        raise _error(exc.status_code, exc.detail) from None
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, DUPLICATE) from None
    _commit(db)
    return _read(_load(db, actor, intervention.id))


@router.get("", response_model=Page[InterventionRead])
def list_interventions(params: Annotated[ListParams, Query()], db: DbSession, user: Manager) -> Page[InterventionRead]:
    stmt = service.visible(db, user, select(Intervention).options(selectinload(Intervention.employee)))
    for value, column in ((params.status, Intervention.status), (params.priority, Intervention.priority),
                          (params.category, Intervention.category), (params.employee_id, Intervention.employee_id),
                          (params.department_id, Employee.department_id), (params.risk_alert_id, Intervention.risk_alert_id)):
        if value is not None:
            stmt = stmt.where(column == value)
    if params.assigned_to_me:
        stmt = stmt.where(Intervention.assigned_to_user_id == user.id)
    if params.assigned_to_user_id is not None:
        stmt = stmt.where(Intervention.assigned_to_user_id == params.assigned_to_user_id)
    if params.q:
        stmt = stmt.where(keyword_condition(params.q, Intervention.title))
    if params.due_from:
        stmt = stmt.where(Intervention.due_date >= params.due_from)
    if params.due_to:
        stmt = stmt.where(Intervention.due_date <= params.due_to)
    if params.overdue is not None:
        overdue = Intervention.status.in_(ACTIVE_STATUSES) & (Intervention.due_date < _today())
        stmt = stmt.where(overdue if params.overdue else ~overdue | Intervention.due_date.is_(None))
    stmt = stmt.order_by(Intervention.updated_at.desc(), Intervention.id.desc())
    items, total = paginate(db, stmt, params)
    return Page[InterventionRead].build([_read(i) for i in items], total, params)


# --- Single intervention --------------------------------------------------------------------------


@router.get("/{intervention_id}", response_model=InterventionRead)
def get_intervention(intervention_id: int, db: DbSession, user: Manager) -> InterventionRead:
    return _read(_load(db, user, intervention_id))


@router.patch("/{intervention_id}", response_model=InterventionRead)
def update_intervention(intervention_id: int, payload: InterventionUpdate, db: DbSession, actor: Manager) -> InterventionRead:
    intervention = _load(db, actor, intervention_id)
    if intervention.status not in ACTIVE_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, f"A {intervention.status.value} intervention cannot be edited")
    _check_version(intervention, payload.expected_version)
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True, exclude={"expected_version"}).items()
               if getattr(intervention, k) != v}
    if not changes:
        return _read(intervention)
    if "due_date" in changes:
        _check_due_date(changes["due_date"])
    if "category" in changes:
        changes["active_alert_key"] = service.active_alert_key(intervention.risk_alert_id, changes["category"], intervention.status)
    fields = sorted(k for k in changes if k != "active_alert_key")
    _mutate(db, intervention, actor, InterventionEventType.UPDATED, changes, {"fields": fields},
            details={"fields": fields}, allowed_from=ACTIVE_STATUSES)
    _commit(db)
    return _read(_load(db, actor, intervention.id))


@router.post("/{intervention_id}/assign", response_model=InterventionRead)
def assign_intervention(intervention_id: int, payload: AssignRequest, db: DbSession, actor: Manager) -> InterventionRead:
    intervention = _load(db, actor, intervention_id)
    if intervention.status not in ACTIVE_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, f"A {intervention.status.value} intervention cannot be reassigned")
    if not service.eligible_assignee(db, db.get(User, payload.user_id), intervention.employee):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "This user cannot be assigned to this intervention")
    if intervention.assigned_to_user_id == payload.user_id:
        return _read(intervention)  # repeat request: no event, no notification
    _check_version(intervention, payload.expected_version)
    previous = intervention.assigned_to_user_id
    details = {"from_user_id": previous, "to_user_id": payload.user_id}
    _mutate(db, intervention, actor, InterventionEventType.ASSIGNED, {"assigned_to_user_id": payload.user_id},
            details, details=details, allowed_from=ACTIVE_STATUSES)
    _notify_assignee(db, intervention.id, intervention.priority.value, payload.user_id, actor)
    _commit(db)
    return _read(_load(db, actor, intervention.id))


def _transition(db: DbSession, actor: User, intervention_id: int, event_type: InterventionEventType,
                expected_version: int | None, extra: dict) -> InterventionRead:
    intervention = _load(db, actor, intervention_id)
    allowed_from, target = service.TRANSITIONS[event_type]
    if intervention.status == target:
        return _read(intervention)  # idempotent repeat
    if intervention.status not in allowed_from:
        raise _error(status.HTTP_409_CONFLICT,
                     f"Cannot move a {intervention.status.value} intervention to {target.value}")
    _check_version(intervention, expected_version)
    values = {"status": target, **extra}
    if target in (InterventionStatus.COMPLETED, InterventionStatus.CANCELLED):
        values["active_alert_key"] = None  # frees the alert/category slot; the alert itself is untouched
    _mutate(db, intervention, actor, event_type, values,
            {"from_status": intervention.status.value, "to_status": target.value,
             "risk_alert_id": intervention.risk_alert_id},
            from_status=intervention.status, to_status=target, allowed_from=allowed_from)
    _commit(db)
    return _read(_load(db, actor, intervention.id))


@router.post("/{intervention_id}/start", response_model=InterventionRead)
def start_intervention(intervention_id: int, payload: VersionedAction, db: DbSession, actor: Manager) -> InterventionRead:
    return _transition(db, actor, intervention_id, InterventionEventType.STARTED, payload.expected_version,
                       {"started_at": utcnow()})


@router.post("/{intervention_id}/complete", response_model=InterventionRead)
def complete_intervention(intervention_id: int, payload: CompleteRequest, db: DbSession, actor: Manager) -> InterventionRead:
    """IN_PROGRESS → COMPLETED with an outcome. The linked risk alert is not resolved automatically."""
    return _transition(db, actor, intervention_id, InterventionEventType.COMPLETED, payload.expected_version,
                       {"completed_at": utcnow(), "outcome": payload.outcome})


@router.post("/{intervention_id}/cancel", response_model=InterventionRead)
def cancel_intervention(intervention_id: int, payload: CancelRequest, db: DbSession, actor: Manager) -> InterventionRead:
    return _transition(db, actor, intervention_id, InterventionEventType.CANCELLED, payload.expected_version,
                       {"cancelled_at": utcnow(), "cancellation_reason": payload.reason})


@router.post("/{intervention_id}/notes", response_model=EventRead, status_code=status.HTTP_201_CREATED)
def add_note(intervention_id: int, payload: NoteRequest, db: DbSession, actor: Manager) -> InterventionEvent:
    """Follow-up notes may be added in any status (including after completion)."""
    intervention = _load(db, actor, intervention_id)
    try:
        version = service.apply_change(db, intervention, actor, utcnow(), InterventionEventType.NOTE_ADDED, {},
                                       note=payload.note)
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT) from None
    record_audit(db, actor=actor, action="intervention.note_added", entity_type="intervention",
                 entity_id=intervention.id, details={"version": version})  # never the note text
    db.commit()
    return db.scalar(select(InterventionEvent).where(InterventionEvent.intervention_id == intervention.id,
                                                     InterventionEvent.version == version))


@router.get("/{intervention_id}/history", response_model=Page[EventRead])
def history(intervention_id: int, params: Annotated[PaginationParams, Query()], db: DbSession, actor: Manager) -> Page[EventRead]:
    intervention = _load(db, actor, intervention_id)
    stmt = (select(InterventionEvent).where(InterventionEvent.intervention_id == intervention.id)
            .order_by(InterventionEvent.version.asc(), InterventionEvent.id.asc()))
    items, total = paginate(db, stmt, params)
    return Page[EventRead].build([EventRead.model_validate(e) for e in items], total, params)
