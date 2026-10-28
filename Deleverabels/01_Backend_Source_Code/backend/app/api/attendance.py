"""Attendance: self-service check-in/out, scoped reports, and audited ADMIN/HR corrections.

Self-service actions always act on the caller's own linked employee record and use server time.
"""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import Attendance, AttendanceStatus, Employee, EmploymentStatus, Shift, User
from app.models.base import utcnow
from app.schemas.attendance import (
    AttendanceCorrection,
    AttendanceRead,
    AttendanceReportFilters,
    AttendanceReportParams,
    AttendanceSummary,
    CheckInRequest,
    CheckOutRequest,
    MyAttendanceParams,
    MySummaryParams,
)
from app.schemas.common import Page
from app.services import attendance_rules as rules
from app.services.audit import apply_changes, record_audit
from app.services.employee_access import apply_scope, employee_scope
from app.services.pagination import paginate

router = APIRouter(prefix="/api/attendance", tags=["attendance"])

SelfService = Annotated[User, Depends(require_roles(*permissions.ATTENDANCE_SELF_SERVICE))]
Reporter = Annotated[User, Depends(require_roles(*permissions.ATTENDANCE_REPORT))]
Corrector = Annotated[User, Depends(require_roles(*permissions.ATTENDANCE_CORRECT))]

NOT_FOUND = "Attendance record not found"
_LOAD_OPTIONS = (selectinload(Attendance.employee), selectinload(Attendance.shift))
ATTENDED = (AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.HALF_DAY)


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _policy() -> rules.AttendancePolicy:
    settings = get_settings()
    return rules.AttendancePolicy(
        tz=settings.org_tz,
        late_grace_minutes=settings.attendance_late_grace_minutes,
        default_overtime_threshold_minutes=settings.attendance_default_overtime_threshold_minutes,
    )


def _own_employee(db: DbSession, user: User) -> Employee:
    employee = db.scalar(select(Employee).where(Employee.user_id == user.id))
    if employee is None:
        raise _error(status.HTTP_404_NOT_FOUND, "No employee profile is linked to your account")
    return employee


def _load(db: DbSession, attendance_id: int) -> Attendance:
    record = db.scalar(
        select(Attendance)
        .options(*_LOAD_OPTIONS)
        .where(Attendance.id == attendance_id)
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return record


def _shift(db: DbSession, shift_id: int, *, require_active: bool) -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Shift does not exist")
    if require_active and not shift.is_active:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Shift is inactive")
    return shift


def _open_record(db: DbSession, employee_id: int) -> Attendance | None:
    return db.scalar(
        select(Attendance)
        .options(*_LOAD_OPTIONS)
        .where(
            Attendance.employee_id == employee_id,
            Attendance.check_in.is_not(None),
            Attendance.check_out.is_(None),
        )
        .order_by(Attendance.check_in.desc())
        .limit(1)
    )


def _date_taken(db: DbSession, employee_id: int, work_date: date, exclude_id: int | None = None) -> bool:
    stmt = select(Attendance.id).where(
        Attendance.employee_id == employee_id, Attendance.work_date == work_date
    )
    if exclude_id is not None:
        stmt = stmt.where(Attendance.id != exclude_id)
    return db.scalar(stmt) is not None


def _commit(db: DbSession) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _error(
            status.HTTP_409_CONFLICT, "Attendance for this employee and work date already exists"
        ) from None


def _page(db: DbSession, stmt: Select, params) -> Page[AttendanceRead]:
    items, total = paginate(db, stmt.options(*_LOAD_OPTIONS), params)
    return Page[AttendanceRead].build([AttendanceRead.model_validate(r) for r in items], total, params)


def _range_conditions(date_from: date | None, date_to: date | None) -> list:
    conditions = []
    if date_from is not None:
        conditions.append(Attendance.work_date >= date_from)
    if date_to is not None:
        conditions.append(Attendance.work_date <= date_to)
    return conditions


def _report_conditions(filters: AttendanceReportFilters) -> list:
    conditions = _range_conditions(filters.date_from, filters.date_to)
    if filters.employee_id is not None:
        conditions.append(Attendance.employee_id == filters.employee_id)
    if filters.department_id is not None:
        conditions.append(Employee.department_id == filters.department_id)
    if filters.shift_id is not None:
        conditions.append(Attendance.shift_id == filters.shift_id)
    if filters.status is not None:
        conditions.append(Attendance.status == filters.status)
    return conditions


def _scoped_report(db: DbSession, user: User, stmt: Select) -> Select:
    """Join employees so department filters and manager scoping can apply."""
    stmt = stmt.join(Employee, Attendance.employee_id == Employee.id)
    return apply_scope(stmt, employee_scope(db, user))


def _summarize(db: DbSession, stmt_filter, date_from: date | None, date_to: date | None) -> AttendanceSummary:
    def count_status(value: AttendanceStatus):
        return func.coalesce(func.sum(case((Attendance.status == value, 1), else_=0)), 0)

    open_count = func.coalesce(
        func.sum(case((and_(Attendance.check_in.is_not(None), Attendance.check_out.is_(None)), 1), else_=0)), 0
    )
    stmt = select(
        func.count(Attendance.id),
        count_status(AttendanceStatus.PRESENT),
        count_status(AttendanceStatus.LATE),
        count_status(AttendanceStatus.HALF_DAY),
        count_status(AttendanceStatus.ABSENT),
        count_status(AttendanceStatus.ON_LEAVE),
        open_count,
        func.coalesce(func.sum(Attendance.work_minutes), 0),
        func.coalesce(func.sum(Attendance.overtime_minutes), 0),
    ).select_from(Attendance)
    records, present, late, half, absent, on_leave, open_records, worked, overtime = db.execute(
        stmt_filter(stmt)
    ).one()

    attended = present + late + half
    denominator = attended + absent
    return AttendanceSummary(
        date_from=date_from,
        date_to=date_to,
        records=records,
        attended_days=attended,
        present_days=present,
        late_days=late,
        half_days=half,
        absent_days=absent,
        on_leave_days=on_leave,
        open_records=open_records,
        work_minutes=worked,
        overtime_minutes=overtime,
        attendance_percentage=round(attended * 100 / denominator, 2) if denominator else None,
    )


# --- Self-service ---------------------------------------------------------------------------------


@router.post("/check-in", response_model=AttendanceRead, status_code=status.HTTP_201_CREATED)
def check_in(payload: CheckInRequest, db: DbSession, user: SelfService) -> Attendance:
    employee = _own_employee(db, user)
    if employee.status != EmploymentStatus.ACTIVE:
        raise _error(status.HTTP_409_CONFLICT, "Only active employees can check in")

    open_record = _open_record(db, employee.id)
    if open_record is not None:
        raise _error(
            status.HTTP_409_CONFLICT,
            f"You are already checked in (work date {open_record.work_date.isoformat()}); check out first",
        )

    shift = _shift(db, payload.shift_id, require_active=True) if payload.shift_id else None
    policy = _policy()
    now = utcnow()
    computed = rules.compute(now, None, 0, shift, policy)

    if _date_taken(db, employee.id, computed.work_date):
        raise _error(
            status.HTTP_409_CONFLICT,
            f"Attendance for {computed.work_date.isoformat()} is already recorded; "
            "ask HR for a correction if it is wrong",
        )

    record = Attendance(
        employee_id=employee.id,
        shift_id=shift.id if shift else None,
        work_date=computed.work_date,
        check_in=now,
        break_minutes=0,
        status=computed.status,
        note=payload.note,
    )
    db.add(record)
    _commit(db)
    return _load(db, record.id)


@router.post("/check-out", response_model=AttendanceRead)
def check_out(payload: CheckOutRequest, db: DbSession, user: SelfService) -> Attendance:
    employee = _own_employee(db, user)
    record = _open_record(db, employee.id)
    if record is None:
        raise _error(status.HTTP_409_CONFLICT, "No active check-in to check out from")

    now = utcnow()
    if now <= record.check_in:
        raise _error(status.HTTP_409_CONFLICT, "Check-out time cannot be before check-in")
    if now - record.check_in > rules.MAX_SESSION:
        raise _error(
            status.HTTP_409_CONFLICT,
            "This check-in is more than 24 hours old; ask HR to correct the record",
        )

    shift = record.shift
    policy = _policy()
    break_minutes = (
        payload.break_minutes if payload.break_minutes is not None
        else (shift.break_minutes if shift else 0)
    )
    try:
        worked = rules.work_minutes(record.check_in, now, break_minutes)
    except rules.AttendanceRuleError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    record.check_out = now
    record.break_minutes = break_minutes
    record.work_minutes = worked
    record.overtime_minutes = rules.overtime_minutes(worked, shift, policy)
    record.status = rules.check_out_status(record.status, worked, shift, policy)
    if payload.note is not None:
        record.note = payload.note
    _commit(db)
    return _load(db, record.id)


@router.get("/me", response_model=Page[AttendanceRead])
def my_attendance(
    params: Annotated[MyAttendanceParams, Query()], db: DbSession, user: SelfService
) -> Page[AttendanceRead]:
    employee = _own_employee(db, user)
    stmt = select(Attendance).where(
        Attendance.employee_id == employee.id, *_range_conditions(params.date_from, params.date_to)
    )
    if params.status is not None:
        stmt = stmt.where(Attendance.status == params.status)
    return _page(db, stmt.order_by(Attendance.work_date.desc(), Attendance.id.desc()), params)


@router.get("/me/summary", response_model=AttendanceSummary)
def my_summary(
    params: Annotated[MySummaryParams, Query()], db: DbSession, user: SelfService
) -> AttendanceSummary:
    """Defaults to the current calendar month (in ORG_TIMEZONE) up to today."""
    employee = _own_employee(db, user)
    today = utcnow().astimezone(get_settings().org_tz).date()
    date_to = params.date_to or today
    date_from = params.date_from or date_to.replace(day=1)
    if date_from > date_to:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be on or before date_to")

    conditions = [Attendance.employee_id == employee.id, *_range_conditions(date_from, date_to)]
    return _summarize(db, lambda stmt: stmt.where(*conditions), date_from, date_to)


# --- Reports (ADMIN/HR organization-wide, MANAGER own department) --------------------------------


@router.get("", response_model=Page[AttendanceRead])
def attendance_report(
    params: Annotated[AttendanceReportParams, Query()], db: DbSession, user: Reporter
) -> Page[AttendanceRead]:
    stmt = _scoped_report(db, user, select(Attendance)).where(*_report_conditions(params))
    return _page(db, stmt.order_by(Attendance.work_date.desc(), Attendance.id.desc()), params)


@router.get("/summary", response_model=AttendanceSummary)
def attendance_report_summary(
    params: Annotated[AttendanceReportFilters, Query()], db: DbSession, user: Reporter
) -> AttendanceSummary:
    conditions = _report_conditions(params)
    return _summarize(
        db,
        lambda stmt: _scoped_report(db, user, stmt).where(*conditions),
        params.date_from,
        params.date_to,
    )


@router.get("/{attendance_id}", response_model=AttendanceRead)
def get_attendance(
    attendance_id: int,
    db: DbSession,
    user: Annotated[User, Depends(require_roles(*permissions.ATTENDANCE_DETAIL))],
) -> Attendance:
    record = _load(db, attendance_id)
    # Out-of-scope records look exactly like missing ones.
    if not employee_scope(db, user).allows(record.employee):
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return record


# --- Corrections ----------------------------------------------------------------------------------


@router.patch("/{attendance_id}/correction", response_model=AttendanceRead)
def correct_attendance(
    attendance_id: int, payload: AttendanceCorrection, db: DbSession, actor: Corrector
) -> Attendance:
    record = _load(db, attendance_id)
    if record.employee.user_id == actor.id:
        raise _error(status.HTTP_403_FORBIDDEN, "You cannot correct your own attendance")

    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})
    shift = record.shift
    if "shift_id" in changes:
        shift = _shift(db, changes["shift_id"], require_active=False) if changes["shift_id"] else None

    check_in_at = changes.get("check_in", record.check_in)
    check_out_at = changes.get("check_out", record.check_out)
    check_in_at = check_in_at.astimezone(UTC) if check_in_at else None
    check_out_at = check_out_at.astimezone(UTC) if check_out_at else None
    break_minutes = changes.get("break_minutes", record.break_minutes)

    updates: dict[str, Any] = {
        "check_in": check_in_at,
        "check_out": check_out_at,
        "break_minutes": break_minutes,
        "shift_id": shift.id if shift else None,
    }
    if check_in_at is None:
        if check_out_at is not None:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "check_out requires a check_in")
        updates["status"] = changes.get("status", record.status)
    else:
        try:
            computed = rules.compute(check_in_at, check_out_at, break_minutes, shift, _policy())
        except rules.AttendanceRuleError as exc:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        if _date_taken(db, record.employee_id, computed.work_date, exclude_id=record.id):
            raise _error(
                status.HTTP_409_CONFLICT,
                f"Another attendance record already exists for {computed.work_date.isoformat()}",
            )
        updates |= {
            "work_date": computed.work_date,
            "work_minutes": computed.work_minutes,
            "overtime_minutes": computed.overtime_minutes,
            "status": changes.get("status", computed.status),
        }

    diff = apply_changes(record, updates)
    if diff:
        # Same transaction as the change: if the audit row cannot be written, nothing is committed.
        record_audit(
            db, actor=actor, action="attendance.corrected", entity_type="attendance",
            entity_id=record.id,
            details={"employee_id": record.employee_id, "changes": diff, "reason": payload.reason},
        )
        _commit(db)
    return _load(db, record.id)
