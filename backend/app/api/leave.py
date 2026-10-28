"""Leave types, policies, requests, approval workflow, balances and attendance conflicts.

Transactions: each endpoint commits once. Request/status changes, balance changes, the audit row and
notifications are all part of that single commit; any failure rolls all of them back.
"""

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    Attendance,
    Department,
    Employee,
    EmploymentStatus,
    LeavePolicy,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
    User,
)
from app.models.base import utcnow
from app.schemas.common import Page
from app.schemas.leave import (
    BalanceParams,
    CancelDecision,
    ConflictParams,
    LeaveBalanceRead,
    LeaveConflictRead,
    LeavePolicyCreate,
    LeavePolicyListParams,
    LeavePolicyRead,
    LeavePolicyUpdate,
    LeaveRequestCreate,
    LeaveRequestRead,
    LeaveReviewParams,
    LeaveTypeCreate,
    LeaveTypeListParams,
    LeaveTypeRead,
    LeaveTypeRef,
    LeaveTypeUpdate,
    MyLeaveParams,
    RejectDecision,
    ReviewDecision,
    validate_policy_dates,
)
from app.services import leave_balance
from app.services.audit import apply_changes, record_audit
from app.services.employee_access import apply_scope, employee_scope
from app.services.leave_attendance import conflict_dates, conflicts_query
from app.services.leave_calendar import count_leave_days, org_today
from app.services.notifications import department_manager_user_ids, notify
from app.services.pagination import paginate

router = APIRouter(prefix="/api/leave", tags=["leave"])

Admin = Annotated[User, Depends(require_roles(*permissions.LEAVE_ADMIN))]
CatalogReader = Annotated[User, Depends(require_roles(*permissions.LEAVE_CATALOG_READ))]
SelfService = Annotated[User, Depends(require_roles(*permissions.LEAVE_SELF_SERVICE))]
Reviewer = Annotated[User, Depends(require_roles(*permissions.LEAVE_REVIEW))]

REQUEST_NOT_FOUND = "Leave request not found"
_REQUEST_LOAD = (selectinload(LeaveRequest.employee), selectinload(LeaveRequest.leave_type))


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _today() -> date:
    return org_today(utcnow(), get_settings().org_tz)


def _commit(db: DbSession, conflict_detail: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, conflict_detail) from None


def _own_employee(db: DbSession, user: User) -> Employee:
    employee = db.scalar(select(Employee).where(Employee.user_id == user.id))
    if employee is None:
        raise _error(status.HTTP_404_NOT_FOUND, "No employee profile is linked to your account")
    return employee


def _date_range(start: date, end: date) -> str:
    return start.isoformat() if start == end else f"{start.isoformat()} to {end.isoformat()}"


# --- Leave types ---------------------------------------------------------------------------------


def _get_type(db: DbSession, leave_type_id: int) -> LeaveType:
    leave_type = db.get(LeaveType, leave_type_id)
    if leave_type is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Leave type not found")
    return leave_type


def _type_conflict(db: DbSession, name: str | None, code: str | None, exclude_id: int | None = None) -> str | None:
    checks = []
    if name is not None:
        checks.append((func.lower(LeaveType.name) == name.lower(), "A leave type with this name already exists"))
    if code is not None:
        checks.append((LeaveType.code == code, "A leave type with this code already exists"))
    for condition, message in checks:
        stmt = select(LeaveType.id).where(condition)
        if exclude_id is not None:
            stmt = stmt.where(LeaveType.id != exclude_id)
        if db.scalar(stmt) is not None:
            return message
    return None


@router.post("/types", response_model=LeaveTypeRead, status_code=status.HTTP_201_CREATED)
def create_leave_type(payload: LeaveTypeCreate, db: DbSession, actor: Admin) -> LeaveType:
    if conflict := _type_conflict(db, payload.name, payload.code):
        raise _error(status.HTTP_409_CONFLICT, conflict)
    leave_type = LeaveType(**payload.model_dump())
    db.add(leave_type)
    db.flush()
    record_audit(db, actor=actor, action="leave_type.created", entity_type="leave_type",
                 entity_id=leave_type.id, details={"code": leave_type.code, "name": leave_type.name})
    _commit(db, "A leave type with this name or code already exists")
    return leave_type


@router.get("/types", response_model=Page[LeaveTypeRead])
def list_leave_types(
    params: Annotated[LeaveTypeListParams, Query()], db: DbSession, user: CatalogReader
) -> Page[LeaveTypeRead]:
    stmt = select(LeaveType).order_by(LeaveType.name.asc(), LeaveType.id.asc())
    if user.role not in permissions.LEAVE_CATALOG_READ_ALL:
        stmt = stmt.where(LeaveType.is_active.is_(True))
    elif params.is_active is not None:
        stmt = stmt.where(LeaveType.is_active.is_(params.is_active))
    items, total = paginate(db, stmt, params)
    return Page[LeaveTypeRead].build([LeaveTypeRead.model_validate(t) for t in items], total, params)


@router.patch("/types/{leave_type_id}", response_model=LeaveTypeRead)
def update_leave_type(
    leave_type_id: int, payload: LeaveTypeUpdate, db: DbSession, actor: Admin
) -> LeaveType:
    leave_type = _get_type(db, leave_type_id)
    changes = payload.model_dump(exclude_unset=True)
    if conflict := _type_conflict(db, changes.get("name"), None, exclude_id=leave_type.id):
        raise _error(status.HTTP_409_CONFLICT, conflict)
    diff = apply_changes(leave_type, changes)
    if diff:
        record_audit(db, actor=actor, action="leave_type.updated", entity_type="leave_type",
                     entity_id=leave_type.id, details={"changes": diff})
        _commit(db, "A leave type with this name already exists")
    return leave_type


# --- Policies -----------------------------------------------------------------------------------------


def _policy_overlaps(
    db: DbSession, leave_type_id: int, department_id: int | None, start: date, end: date | None,
    exclude_id: int | None = None,
) -> bool:
    stmt = select(LeavePolicy.id).where(
        LeavePolicy.leave_type_id == leave_type_id,
        LeavePolicy.department_id.is_(None) if department_id is None else LeavePolicy.department_id == department_id,
        LeavePolicy.is_active.is_(True),
        (LeavePolicy.effective_to.is_(None)) | (LeavePolicy.effective_to >= start),
    )
    if end is not None:
        stmt = stmt.where(LeavePolicy.effective_from <= end)
    if exclude_id is not None:
        stmt = stmt.where(LeavePolicy.id != exclude_id)
    return db.scalar(stmt) is not None


OVERLAPPING_POLICY = "An active policy for this leave type and scope already covers part of this period"


def _load_policy(db: DbSession, policy_id: int) -> LeavePolicy:
    policy = db.scalar(
        select(LeavePolicy).options(selectinload(LeavePolicy.leave_type)).where(LeavePolicy.id == policy_id)
        .execution_options(populate_existing=True)
    )
    if policy is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Leave policy not found")
    return policy


@router.post("/policies", response_model=LeavePolicyRead, status_code=status.HTTP_201_CREATED)
def create_leave_policy(payload: LeavePolicyCreate, db: DbSession, actor: Admin) -> LeavePolicy:
    if db.get(LeaveType, payload.leave_type_id) is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Leave type does not exist")
    if payload.department_id is not None and db.get(Department, payload.department_id) is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Department does not exist")
    if payload.is_active and _policy_overlaps(
        db, payload.leave_type_id, payload.department_id, payload.effective_from, payload.effective_to
    ):
        raise _error(status.HTTP_409_CONFLICT, OVERLAPPING_POLICY)

    policy = LeavePolicy(**payload.model_dump())
    db.add(policy)
    db.flush()
    record_audit(db, actor=actor, action="leave_policy.created", entity_type="leave_policy",
                 entity_id=policy.id, details=payload.model_dump(mode="json"))
    _commit(db, OVERLAPPING_POLICY)
    return _load_policy(db, policy.id)


@router.get("/policies", response_model=Page[LeavePolicyRead])
def list_leave_policies(
    params: Annotated[LeavePolicyListParams, Query()], db: DbSession, user: CatalogReader
) -> Page[LeavePolicyRead]:
    stmt = select(LeavePolicy).options(selectinload(LeavePolicy.leave_type))
    if params.leave_type_id is not None:
        stmt = stmt.where(LeavePolicy.leave_type_id == params.leave_type_id)
    if params.department_id is not None:
        stmt = stmt.where(LeavePolicy.department_id == params.department_id)

    if user.role in permissions.LEAVE_CATALOG_READ_ALL:
        if params.is_active is not None:
            stmt = stmt.where(LeavePolicy.is_active.is_(params.is_active))
    else:
        # Others see active policies of active leave types that apply to them.
        own = db.scalar(select(Employee).where(Employee.user_id == user.id))
        applicable = LeavePolicy.department_id.is_(None)
        if own is not None:
            applicable = applicable | (LeavePolicy.department_id == own.department_id)
        stmt = (
            stmt.join(LeaveType, LeaveType.id == LeavePolicy.leave_type_id)
            .where(LeavePolicy.is_active.is_(True), LeaveType.is_active.is_(True), applicable)
        )

    stmt = stmt.order_by(LeavePolicy.leave_type_id, LeavePolicy.effective_from.desc(), LeavePolicy.id.desc())
    items, total = paginate(db, stmt, params)
    return Page[LeavePolicyRead].build([LeavePolicyRead.model_validate(p) for p in items], total, params)


@router.patch("/policies/{policy_id}", response_model=LeavePolicyRead)
def update_leave_policy(
    policy_id: int, payload: LeavePolicyUpdate, db: DbSession, actor: Admin
) -> LeavePolicy:
    policy = _load_policy(db, policy_id)
    changes = payload.model_dump(exclude_unset=True)
    effective_from = changes.get("effective_from", policy.effective_from)
    effective_to = changes.get("effective_to", policy.effective_to)
    is_active = changes.get("is_active", policy.is_active)
    try:
        validate_policy_dates(effective_from, effective_to)
    except ValueError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if is_active and _policy_overlaps(
        db, policy.leave_type_id, policy.department_id, effective_from, effective_to, exclude_id=policy.id
    ):
        raise _error(status.HTTP_409_CONFLICT, OVERLAPPING_POLICY)

    diff = apply_changes(policy, changes)
    if diff:
        record_audit(db, actor=actor, action="leave_policy.updated", entity_type="leave_policy",
                     entity_id=policy.id, details={"changes": diff})
        _commit(db, OVERLAPPING_POLICY)
    return _load_policy(db, policy.id)


# --- Requests: helpers ------------------------------------------------------------------------------


def _load_request(db: DbSession, request_id: int) -> LeaveRequest:
    request = db.scalar(
        select(LeaveRequest).options(*_REQUEST_LOAD).where(LeaveRequest.id == request_id)
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise _error(status.HTTP_404_NOT_FOUND, REQUEST_NOT_FOUND)
    return request


def _transition(db: DbSession, request: LeaveRequest, from_status: LeaveRequestStatus, values: dict[str, Any]) -> None:
    """Atomic status change: succeeds only if the row still has from_status (blocks double processing)."""
    result = db.execute(
        update(LeaveRequest)
        .where(LeaveRequest.id == request.id, LeaveRequest.status == from_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "This leave request was already processed")


def _balance_step(db: DbSession, step, request: LeaveRequest) -> None:
    try:
        balance = leave_balance.balance_for_request(db, request.employee_id, request.leave_type_id, request.leave_year)
        step(db, balance, request.working_days)
    except leave_balance.BalanceStateError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Leave balance changed concurrently; please retry") from None


def _check_review_access(db: DbSession, reviewer: User, request: LeaveRequest) -> None:
    if not employee_scope(db, reviewer).allows(request.employee):
        raise _error(status.HTTP_404_NOT_FOUND, REQUEST_NOT_FOUND)
    if request.employee.user_id == reviewer.id:
        raise _error(status.HTTP_403_FORBIDDEN, "You cannot review your own leave request")
    if request.status != LeaveRequestStatus.PENDING:
        raise _error(status.HTTP_409_CONFLICT, f"Leave request is already {request.status.value.lower()}")


def _notify_employee(db: DbSession, request: LeaveRequest, title: str, outcome: str) -> None:
    if request.employee.user_id is None:
        return
    notify(
        db, user_id=request.employee.user_id, category="leave", title=title,
        message=f"Your {request.leave_type.name} request for "
                f"{_date_range(request.start_date, request.end_date)} was {outcome}.",
        entity_type="leave_request", entity_id=request.id,
    )


def _request_page(db: DbSession, stmt: Select, params) -> Page[LeaveRequestRead]:
    items, total = paginate(db, stmt.options(*_REQUEST_LOAD), params)
    return Page[LeaveRequestRead].build([LeaveRequestRead.model_validate(r) for r in items], total, params)


def _window(stmt: Select, date_from: date | None, date_to: date | None) -> Select:
    """Requests overlapping the window."""
    if date_from is not None:
        stmt = stmt.where(LeaveRequest.end_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(LeaveRequest.start_date <= date_to)
    return stmt


# --- Requests: self-service ---------------------------------------------------------------------------


@router.post("/requests", response_model=LeaveRequestRead, status_code=status.HTTP_201_CREATED)
def create_leave_request(payload: LeaveRequestCreate, db: DbSession, user: SelfService) -> LeaveRequest:
    settings = get_settings()
    employee = _own_employee(db, user)
    if employee.status == EmploymentStatus.INACTIVE:
        raise _error(status.HTTP_409_CONFLICT, "Inactive employees cannot request leave")

    leave_type = db.get(LeaveType, payload.leave_type_id)
    if leave_type is None or not leave_type.is_active:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Leave type does not exist or is inactive")

    today = _today()
    if payload.start_date < today - timedelta(days=settings.leave_max_backdate_days):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"Leave cannot start more than {settings.leave_max_backdate_days} days in the past")
    if payload.start_date.year > today.year + 1:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Leave can be requested for this year or next year only")

    policy = leave_balance.resolve_policy(db, leave_type.id, employee.department_id, payload.start_date)
    if policy is None or (policy.effective_to is not None and policy.effective_to < payload.end_date):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "No active leave policy covers these dates")

    working_days = count_leave_days(
        payload.start_date, payload.end_date, settings.leave_weekend_days, policy.count_weekends
    )
    if working_days == 0:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The selected dates contain no working days")
    if policy.max_consecutive_days is not None and working_days > policy.max_consecutive_days:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"This leave type allows at most {policy.max_consecutive_days} consecutive day(s)")

    # Serialize submissions per employee (row lock on MySQL; SQLite ignores FOR UPDATE and serializes writes).
    db.execute(select(Employee.id).where(Employee.id == employee.id).with_for_update())
    overlapping = db.scalar(
        select(LeaveRequest.id).where(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.status.in_([LeaveRequestStatus.PENDING, LeaveRequestStatus.APPROVED]),
            LeaveRequest.start_date <= payload.end_date,
            LeaveRequest.end_date >= payload.start_date,
        )
    )
    if overlapping is not None:
        raise _error(status.HTTP_409_CONFLICT, "You already have a pending or approved request overlapping these dates")

    balance = leave_balance.ensure_balance(db, employee.id, policy, payload.start_date.year)
    try:
        leave_balance.reserve(db, balance, working_days)
    except leave_balance.InsufficientBalanceError as exc:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    request = LeaveRequest(
        employee_id=employee.id, leave_type_id=leave_type.id, policy_id=policy.id,
        start_date=payload.start_date, end_date=payload.end_date, working_days=working_days,
        reason=payload.reason, status=LeaveRequestStatus.PENDING,
    )
    db.add(request)
    db.flush()
    # The reason text is personal information and stays on the request, not in the audit log.
    record_audit(db, actor=user, action="leave.requested", entity_type="leave_request", entity_id=request.id,
                 details={"employee_id": employee.id, "leave_type": leave_type.code,
                          "start_date": payload.start_date.isoformat(), "end_date": payload.end_date.isoformat(),
                          "working_days": working_days})
    for manager_id in department_manager_user_ids(db, employee.department_id, exclude_user_id=user.id):
        notify(db, user_id=manager_id, category="leave", title="Leave request awaiting review",
               message=f"{employee.full_name} requested {leave_type.name} for "
                       f"{_date_range(payload.start_date, payload.end_date)}.",
               entity_type="leave_request", entity_id=request.id)
    _commit(db, "Leave request could not be saved; please retry")
    return _load_request(db, request.id)


@router.get("/requests/me", response_model=Page[LeaveRequestRead])
def my_leave_requests(
    params: Annotated[MyLeaveParams, Query()], db: DbSession, user: SelfService
) -> Page[LeaveRequestRead]:
    employee = _own_employee(db, user)
    stmt = _window(select(LeaveRequest).where(LeaveRequest.employee_id == employee.id), params.date_from, params.date_to)
    if params.status is not None:
        stmt = stmt.where(LeaveRequest.status == params.status)
    if params.leave_type_id is not None:
        stmt = stmt.where(LeaveRequest.leave_type_id == params.leave_type_id)
    return _request_page(db, stmt.order_by(LeaveRequest.start_date.desc(), LeaveRequest.id.desc()), params)


@router.get("/balances/me", response_model=list[LeaveBalanceRead])
def my_leave_balances(
    params: Annotated[BalanceParams, Query()], db: DbSession, user: SelfService
) -> list[LeaveBalanceRead]:
    """Read-only: stored balances, or what the applicable policy would allocate. Never writes."""
    employee = _own_employee(db, user)
    today = _today()
    year = params.year or today.year
    on_date = today if year == today.year else date(year, 1, 1)

    results = []
    for leave_type in db.scalars(select(LeaveType).where(LeaveType.is_active.is_(True)).order_by(LeaveType.name)):
        policy = leave_balance.resolve_policy(db, leave_type.id, employee.department_id, on_date)
        view = leave_balance.balance_view(db, employee.id, policy, leave_type.id, year)
        if view is None:
            continue
        results.append(LeaveBalanceRead(
            leave_type=LeaveTypeRef.model_validate(leave_type), year=year,
            allocated_days=view.allocated_days, carried_forward_days=view.carried_forward_days,
            used_days=view.used_days, pending_days=view.pending_days, available_days=view.available_days,
            policy_id=policy.id if policy else None,
        ))
    return results


# --- Requests: review ---------------------------------------------------------------------------------


@router.get("/requests", response_model=Page[LeaveRequestRead])
def review_queue(
    params: Annotated[LeaveReviewParams, Query()], db: DbSession, reviewer: Reviewer
) -> Page[LeaveRequestRead]:
    """ADMIN/HR: organization-wide. MANAGER: own department only."""
    stmt = select(LeaveRequest).join(Employee, Employee.id == LeaveRequest.employee_id)
    stmt = _window(apply_scope(stmt, employee_scope(db, reviewer)), params.date_from, params.date_to)
    for value, column in (
        (params.status, LeaveRequest.status), (params.employee_id, LeaveRequest.employee_id),
        (params.department_id, Employee.department_id), (params.leave_type_id, LeaveRequest.leave_type_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return _request_page(db, stmt.order_by(LeaveRequest.start_date.desc(), LeaveRequest.id.desc()), params)


@router.get("/requests/{request_id}", response_model=LeaveRequestRead)
def get_leave_request(request_id: int, db: DbSession, user: SelfService) -> LeaveRequest:
    request = _load_request(db, request_id)
    is_owner = request.employee.user_id == user.id
    can_review = user.role in permissions.LEAVE_REVIEW and employee_scope(db, user).allows(request.employee)
    if not (is_owner or can_review):
        raise _error(status.HTTP_404_NOT_FOUND, REQUEST_NOT_FOUND)
    return request


@router.post("/requests/{request_id}/approve", response_model=LeaveRequestRead)
def approve_leave_request(
    request_id: int, payload: ReviewDecision, db: DbSession, reviewer: Reviewer
) -> LeaveRequest:
    request = _load_request(db, request_id)
    _check_review_access(db, reviewer, request)

    _transition(db, request, LeaveRequestStatus.PENDING, {
        "status": LeaveRequestStatus.APPROVED, "reviewed_by_user_id": reviewer.id,
        "reviewed_at": utcnow(), "review_comment": payload.comment,
    })
    _balance_step(db, leave_balance.consume, request)
    conflicts = conflict_dates(db, request)
    record_audit(db, actor=reviewer, action="leave.approved", entity_type="leave_request", entity_id=request.id,
                 details={"employee_id": request.employee_id, "working_days": request.working_days,
                          "attendance_conflict_dates": [d.isoformat() for d in conflicts]})
    _notify_employee(db, request, "Leave approved", "approved")
    _commit(db, "This leave request was already processed")
    return _load_request(db, request.id)


@router.post("/requests/{request_id}/reject", response_model=LeaveRequestRead)
def reject_leave_request(
    request_id: int, payload: RejectDecision, db: DbSession, reviewer: Reviewer
) -> LeaveRequest:
    request = _load_request(db, request_id)
    _check_review_access(db, reviewer, request)

    _transition(db, request, LeaveRequestStatus.PENDING, {
        "status": LeaveRequestStatus.REJECTED, "reviewed_by_user_id": reviewer.id,
        "reviewed_at": utcnow(), "review_comment": payload.comment,
    })
    _balance_step(db, leave_balance.release_pending, request)
    record_audit(db, actor=reviewer, action="leave.rejected", entity_type="leave_request", entity_id=request.id,
                 details={"employee_id": request.employee_id, "working_days": request.working_days})
    _notify_employee(db, request, "Leave rejected", "rejected")
    _commit(db, "This leave request was already processed")
    return _load_request(db, request.id)


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestRead)
def cancel_leave_request(
    request_id: int, payload: CancelDecision, db: DbSession, user: SelfService
) -> LeaveRequest:
    """Owner: PENDING any time; APPROVED only before the start date (org time zone).
    ADMIN/HR (not on their own request): PENDING or APPROVED at any time, with a comment."""
    request = _load_request(db, request_id)
    is_owner = request.employee.user_id == user.id
    privileged = not is_owner and user.role in permissions.LEAVE_CANCEL_ANY
    if not (is_owner or privileged):
        if user.role in permissions.LEAVE_REVIEW and employee_scope(db, user).allows(request.employee):
            raise _error(status.HTTP_403_FORBIDDEN, "Only the employee or HR can cancel this request")
        raise _error(status.HTTP_404_NOT_FOUND, REQUEST_NOT_FOUND)

    previous = request.status
    if previous not in (LeaveRequestStatus.PENDING, LeaveRequestStatus.APPROVED):
        raise _error(status.HTTP_409_CONFLICT, f"Leave request is already {previous.value.lower()}")
    if privileged and not payload.comment:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     "A comment is required when cancelling another employee's request")
    if previous == LeaveRequestStatus.APPROVED and not privileged and request.start_date <= _today():
        raise _error(status.HTTP_409_CONFLICT,
                     "Approved leave that has already started can only be cancelled by HR")

    _transition(db, request, previous, {
        "status": LeaveRequestStatus.CANCELLED, "cancelled_by_user_id": user.id,
        "cancelled_at": utcnow(), "cancellation_comment": payload.comment,
    })
    step = leave_balance.release_pending if previous == LeaveRequestStatus.PENDING else leave_balance.restore_used
    _balance_step(db, step, request)
    record_audit(db, actor=user, action="leave.cancelled", entity_type="leave_request", entity_id=request.id,
                 details={"employee_id": request.employee_id, "previous_status": previous.value,
                          "working_days": request.working_days, "cancelled_by_owner": is_owner})
    if privileged:
        _notify_employee(db, request, "Leave cancelled", "cancelled by HR")
    _commit(db, "This leave request was already processed")
    return _load_request(db, request.id)


# --- Attendance conflicts -------------------------------------------------------------------------


@router.get("/conflicts", response_model=Page[LeaveConflictRead])
def leave_attendance_conflicts(
    params: Annotated[ConflictParams, Query()], db: DbSession, reviewer: Reviewer
) -> Page[LeaveConflictRead]:
    """Check-ins recorded on dates covered by approved leave, for HR review. Nothing is changed automatically."""
    stmt = conflicts_query().join(Employee, Employee.id == Attendance.employee_id)
    stmt = apply_scope(stmt, employee_scope(db, reviewer))
    if params.date_from is not None:
        stmt = stmt.where(Attendance.work_date >= params.date_from)
    if params.date_to is not None:
        stmt = stmt.where(Attendance.work_date <= params.date_to)
    if params.employee_id is not None:
        stmt = stmt.where(Attendance.employee_id == params.employee_id)
    if params.department_id is not None:
        stmt = stmt.where(Employee.department_id == params.department_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.options(selectinload(Attendance.employee), selectinload(LeaveRequest.leave_type))
        .order_by(Attendance.work_date.desc(), Attendance.id.desc())
        .limit(params.page_size).offset(params.offset)
    ).all()
    items = [
        LeaveConflictRead(
            attendance_id=attendance.id, leave_request_id=request.id,
            employee=attendance.employee, work_date=attendance.work_date,
            attendance_status=attendance.status.value,
            leave_type=LeaveTypeRef.model_validate(request.leave_type),
        )
        for attendance, request in rows
    ]
    return Page[LeaveConflictRead].build(items, total, params)
