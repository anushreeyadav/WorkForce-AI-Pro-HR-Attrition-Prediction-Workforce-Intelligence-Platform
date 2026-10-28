"""Payroll administration (ADMIN/HR) and employee self-service payslips.

Each endpoint commits once; the change, its audit row and any notification succeed or fail together.
Audit rows carry ids, counts and field names only, never salary amounts or itemized pay.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    CalculationMethod,
    Department,
    Employee,
    PayrollPeriod,
    PayrollProfile,
    PayrollProfileComponent,
    PayrollRecord,
    PayrollStatus,
    SalaryComponent,
    SalaryComponentKind,
    User,
)
from app.models.base import utcnow
from app.schemas.common import Page
from app.schemas.payroll import (
    CalculateRequest,
    CalculationResult,
    ComponentListParams,
    LineItemRead,
    MarkPaidRequest,
    MyPayrollParams,
    PayrollPeriodCreate,
    PayrollPeriodRead,
    PayrollProfileCreate,
    PayrollProfileRead,
    PayrollProfileUpdate,
    PayrollRecordRead,
    PayrollRecordSummary,
    Payslip,
    PayslipEmployee,
    PeriodListParams,
    PeriodRef,
    ProfileComponentIn,
    ProfileComponentRead,
    ProfileListParams,
    RecordListParams,
    SalaryComponentCreate,
    SalaryComponentRead,
    SalaryComponentUpdate,
)
from app.schemas.attendance import AttendanceEmployeeRef
from app.services import payroll as payroll_service
from app.services.audit import apply_changes, record_audit
from app.services.notifications import notify
from app.services.pagination import paginate

router = APIRouter(prefix="/api/payroll", tags=["payroll"])

Admin = Annotated[User, Depends(require_roles(*permissions.PAYROLL_ADMIN))]
SelfService = Annotated[User, Depends(require_roles(*permissions.PAYROLL_SELF_SERVICE))]

RECORD_NOT_FOUND = "Payroll record not found"
EMPLOYEE_VISIBLE = (PayrollStatus.APPROVED, PayrollStatus.PAID)
_RECORD_LOAD = (selectinload(PayrollRecord.employee), selectinload(PayrollRecord.period))
_PROFILE_LOAD = (
    selectinload(PayrollProfile.employee),
    selectinload(PayrollProfile.components).selectinload(PayrollProfileComponent.component),
)


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _commit(db: DbSession, conflict_detail: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, conflict_detail) from None


def _own_employee(db: DbSession, user: User) -> Employee | None:
    return db.scalar(select(Employee).where(Employee.user_id == user.id))


def _forbid_self(db: DbSession, user: User, employee_id: int, action: str) -> None:
    own = _own_employee(db, user)
    if own is not None and own.id == employee_id:
        raise _error(status.HTTP_403_FORBIDDEN, f"You cannot {action} your own payroll")


# --- Components --------------------------------------------------------------------------------


def _get_component(db: DbSession, component_id: int) -> SalaryComponent:
    component = db.get(SalaryComponent, component_id)
    if component is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Salary component not found")
    return component


def _component_conflict(db: DbSession, code: str | None, name: str | None, exclude_id: int | None = None) -> str | None:
    for condition, message in (
        (SalaryComponent.code == code if code else None, "A component with this code already exists"),
        (func.lower(SalaryComponent.name) == name.lower() if name else None, "A component with this name already exists"),
    ):
        if condition is None:
            continue
        stmt = select(SalaryComponent.id).where(condition)
        if exclude_id is not None:
            stmt = stmt.where(SalaryComponent.id != exclude_id)
        if db.scalar(stmt) is not None:
            return message
    return None


@router.post("/components", response_model=SalaryComponentRead, status_code=status.HTTP_201_CREATED)
def create_component(payload: SalaryComponentCreate, db: DbSession, actor: Admin) -> SalaryComponent:
    if conflict := _component_conflict(db, payload.code, payload.name):
        raise _error(status.HTTP_409_CONFLICT, conflict)
    component = SalaryComponent(**payload.model_dump())
    db.add(component)
    db.flush()
    record_audit(db, actor=actor, action="payroll.component.created", entity_type="salary_component",
                 entity_id=component.id, details={"code": component.code, "kind": component.kind.value,
                                                  "calculation_method": component.calculation_method.value})
    _commit(db, "A component with this code or name already exists")
    return component


@router.get("/components", response_model=Page[SalaryComponentRead])
def list_components(params: Annotated[ComponentListParams, Query()], db: DbSession, _: Admin) -> Page[SalaryComponentRead]:
    stmt = select(SalaryComponent).order_by(SalaryComponent.kind, SalaryComponent.display_order, SalaryComponent.code)
    if params.kind is not None:
        stmt = stmt.where(SalaryComponent.kind == params.kind)
    if params.is_active is not None:
        stmt = stmt.where(SalaryComponent.is_active.is_(params.is_active))
    items, total = paginate(db, stmt, params)
    return Page[SalaryComponentRead].build([SalaryComponentRead.model_validate(c) for c in items], total, params)


@router.patch("/components/{component_id}", response_model=SalaryComponentRead)
def update_component(component_id: int, payload: SalaryComponentUpdate, db: DbSession, actor: Admin) -> SalaryComponent:
    component = _get_component(db, component_id)
    changes = payload.model_dump(exclude_unset=True)
    if conflict := _component_conflict(db, None, changes.get("name"), exclude_id=component.id):
        raise _error(status.HTTP_409_CONFLICT, conflict)
    diff = apply_changes(component, changes)
    if diff:
        record_audit(db, actor=actor, action="payroll.component.updated", entity_type="salary_component",
                     entity_id=component.id, details={"changes": diff})
        _commit(db, "A component with this name already exists")
    return component


# --- Profiles ----------------------------------------------------------------------------------


def _profile_read(profile: PayrollProfile) -> PayrollProfileRead:
    return PayrollProfileRead(
        id=profile.id, employee=AttendanceEmployeeRef.model_validate(profile.employee),
        pay_frequency=profile.pay_frequency, base_salary=profile.base_salary,
        effective_from=profile.effective_from, effective_to=profile.effective_to, is_active=profile.is_active,
        components=[
            ProfileComponentRead(component_id=item.component_id, code=item.component.code, name=item.component.name,
                                 kind=item.component.kind, calculation_method=item.component.calculation_method,
                                 amount=item.amount, percentage=item.percentage)
            for item in profile.components
        ],
        created_at=profile.created_at, updated_at=profile.updated_at,
    )


def _load_profile(db: DbSession, profile_id: int) -> PayrollProfile:
    profile = db.scalar(select(PayrollProfile).options(*_PROFILE_LOAD).where(PayrollProfile.id == profile_id)
                        .execution_options(populate_existing=True))
    if profile is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Payroll profile not found")
    return profile


def _build_components(db: DbSession, items: list[ProfileComponentIn]) -> list[PayrollProfileComponent]:
    rows = []
    for item in items:
        component = db.get(SalaryComponent, item.component_id)
        if component is None or not component.is_active:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Salary component {item.component_id} does not exist or is inactive")
        fixed = component.calculation_method == CalculationMethod.FIXED
        if fixed and item.amount is None:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{component.code} is FIXED: provide amount")
        if not fixed and item.percentage is None:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{component.code} is percentage-based: provide percentage")
        rows.append(PayrollProfileComponent(component_id=component.id, amount=item.amount, percentage=item.percentage))
    return rows


OVERLAPPING_PROFILE = "An active payroll profile for this employee already covers part of this period"


@router.post("/profiles", response_model=PayrollProfileRead, status_code=status.HTTP_201_CREATED)
def create_profile(payload: PayrollProfileCreate, db: DbSession, actor: Admin) -> PayrollProfileRead:
    if db.get(Employee, payload.employee_id) is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Employee does not exist")
    _forbid_self(db, actor, payload.employee_id, "edit")
    if payroll_service.overlapping_profiles(db, payload.employee_id, payload.effective_from, payload.effective_to):
        raise _error(status.HTTP_409_CONFLICT, OVERLAPPING_PROFILE)

    profile = PayrollProfile(**payload.model_dump(exclude={"components"}))
    profile.components = _build_components(db, payload.components)
    db.add(profile)
    db.flush()
    record_audit(db, actor=actor, action="payroll.profile.created", entity_type="payroll_profile", entity_id=profile.id,
                 details={"employee_id": profile.employee_id, "effective_from": profile.effective_from.isoformat(),
                          "component_count": len(profile.components)})
    _commit(db, OVERLAPPING_PROFILE)
    return _profile_read(_load_profile(db, profile.id))


@router.get("/profiles", response_model=Page[PayrollProfileRead])
def list_profiles(params: Annotated[ProfileListParams, Query()], db: DbSession, _: Admin) -> Page[PayrollProfileRead]:
    stmt = select(PayrollProfile).options(*_PROFILE_LOAD).order_by(PayrollProfile.employee_id, PayrollProfile.effective_from.desc(), PayrollProfile.id)
    if params.employee_id is not None:
        stmt = stmt.where(PayrollProfile.employee_id == params.employee_id)
    if params.is_active is not None:
        stmt = stmt.where(PayrollProfile.is_active.is_(params.is_active))
    items, total = paginate(db, stmt, params)
    return Page[PayrollProfileRead].build([_profile_read(p) for p in items], total, params)


@router.get("/profiles/{profile_id}", response_model=PayrollProfileRead)
def get_profile(profile_id: int, db: DbSession, _: Admin) -> PayrollProfileRead:
    return _profile_read(_load_profile(db, profile_id))


@router.patch("/profiles/{profile_id}", response_model=PayrollProfileRead)
def update_profile(profile_id: int, payload: PayrollProfileUpdate, db: DbSession, actor: Admin) -> PayrollProfileRead:
    profile = _load_profile(db, profile_id)
    _forbid_self(db, actor, profile.employee_id, "edit")
    changes = payload.model_dump(exclude_unset=True, exclude={"components"})
    start = changes.get("effective_from", profile.effective_from)
    end = changes.get("effective_to", profile.effective_to)
    if end is not None and end < start:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "effective_to must be on or after effective_from")
    if changes.get("is_active", profile.is_active) and payroll_service.overlapping_profiles(
        db, profile.employee_id, start, end, exclude_id=profile.id
    ):
        raise _error(status.HTTP_409_CONFLICT, OVERLAPPING_PROFILE)

    changed_fields = sorted(apply_changes(profile, changes))
    if payload.components is not None:
        profile.components = _build_components(db, payload.components)
        changed_fields.append("components")
    if changed_fields:
        # Field names only: salary values are not copied into the audit log.
        record_audit(db, actor=actor, action="payroll.profile.updated", entity_type="payroll_profile",
                     entity_id=profile.id, details={"employee_id": profile.employee_id, "fields": changed_fields})
        _commit(db, OVERLAPPING_PROFILE)
    return _profile_read(_load_profile(db, profile.id))


# --- Periods -----------------------------------------------------------------------------------


def _record_counts(db: DbSession, period_ids: list[int]) -> dict[int, dict[str, int]]:
    counts: dict[int, dict[str, int]] = {pid: {s.value: 0 for s in PayrollStatus} for pid in period_ids}
    if period_ids:
        for pid, record_status, n in db.execute(
            select(PayrollRecord.period_id, PayrollRecord.status, func.count(PayrollRecord.id))
            .where(PayrollRecord.period_id.in_(period_ids))
            .group_by(PayrollRecord.period_id, PayrollRecord.status)
        ):
            counts[pid][record_status.value] = n
    return counts


def _period_read(period: PayrollPeriod, counts: dict[str, int]) -> PayrollPeriodRead:
    return PayrollPeriodRead(id=period.id, year=period.year, month=period.month, start_date=period.start_date,
                             end_date=period.end_date, record_counts=counts, created_at=period.created_at)


def _get_period(db: DbSession, period_id: int) -> PayrollPeriod:
    period = db.get(PayrollPeriod, period_id)
    if period is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Payroll period not found")
    return period


@router.post("/periods", response_model=PayrollPeriodRead, status_code=status.HTTP_201_CREATED)
def create_period(payload: PayrollPeriodCreate, db: DbSession, actor: Admin) -> PayrollPeriodRead:
    duplicate = "A payroll period for this month already exists"
    if db.scalar(select(PayrollPeriod.id).where(PayrollPeriod.year == payload.year, PayrollPeriod.month == payload.month)):
        raise _error(status.HTTP_409_CONFLICT, duplicate)
    start, end = payroll_service.month_bounds(payload.year, payload.month)
    period = PayrollPeriod(year=payload.year, month=payload.month, start_date=start, end_date=end,
                           created_by_user_id=actor.id)
    db.add(period)
    db.flush()
    record_audit(db, actor=actor, action="payroll.period.created", entity_type="payroll_period", entity_id=period.id,
                 details={"year": payload.year, "month": payload.month})
    _commit(db, duplicate)
    return _period_read(period, _record_counts(db, [period.id])[period.id])


@router.get("/periods", response_model=Page[PayrollPeriodRead])
def list_periods(params: Annotated[PeriodListParams, Query()], db: DbSession, _: Admin) -> Page[PayrollPeriodRead]:
    stmt = select(PayrollPeriod).order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc())
    if params.year is not None:
        stmt = stmt.where(PayrollPeriod.year == params.year)
    items, total = paginate(db, stmt, params)
    counts = _record_counts(db, [p.id for p in items])
    return Page[PayrollPeriodRead].build([_period_read(p, counts[p.id]) for p in items], total, params)


@router.get("/periods/{period_id}", response_model=PayrollPeriodRead)
def get_period(period_id: int, db: DbSession, _: Admin) -> PayrollPeriodRead:
    period = _get_period(db, period_id)
    return _period_read(period, _record_counts(db, [period.id])[period.id])


@router.post("/periods/{period_id}/calculate", response_model=CalculationResult)
def calculate_period(period_id: int, payload: CalculateRequest, db: DbSession, actor: Admin) -> CalculationResult:
    """Create or recalculate CALCULATED records. APPROVED/PAID records are never touched."""
    period = _get_period(db, period_id)
    try:
        summary = payroll_service.calculate_period(db, period, actor, get_settings(), utcnow(), payload.employee_ids)
    except payroll_service.PayrollConflictError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Payroll changed during calculation; please retry") from None
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Payroll changed during calculation; please retry") from None

    record_audit(db, actor=actor, action="payroll.period.calculated", entity_type="payroll_period",
                 entity_id=period.id, details={"created": len(summary.created),
                                               "recalculated": len(summary.recalculated),
                                               "skipped": len(summary.skipped)})
    _commit(db, "Payroll changed during calculation; please retry")
    return CalculationResult(period_id=period.id, created=len(summary.created), recalculated=len(summary.recalculated),
                             record_ids=summary.created + summary.recalculated, skipped=summary.skipped)


# --- Records -----------------------------------------------------------------------------------


def _summary(record: PayrollRecord) -> dict[str, Any]:
    return dict(
        id=record.id, period=PeriodRef.model_validate(record.period),
        employee=AttendanceEmployeeRef.model_validate(record.employee), status=record.status,
        currency=record.currency, gross_earnings=record.gross_earnings, total_deductions=record.total_deductions,
        net_pay=record.net_pay, has_warnings=bool(record.warnings), calculated_at=record.calculated_at,
        approved_at=record.approved_at, paid_at=record.paid_at,
    )


def _split_lines(record: PayrollRecord) -> tuple[list[LineItemRead], list[LineItemRead]]:
    earnings = [LineItemRead.model_validate(i) for i in record.line_items if i.kind == SalaryComponentKind.EARNING]
    deductions = [LineItemRead.model_validate(i) for i in record.line_items if i.kind != SalaryComponentKind.EARNING]
    return earnings, deductions


def _record_read(record: PayrollRecord) -> PayrollRecordRead:
    earnings, deductions = _split_lines(record)
    return PayrollRecordRead(
        **_summary(record), profile_id=record.profile_id, base_salary=record.base_salary,
        warnings=record.warnings or [], earnings=earnings, deductions=deductions,
        calculated_by_user_id=record.calculated_by_user_id, approved_by_user_id=record.approved_by_user_id,
        paid_by_user_id=record.paid_by_user_id, payment_reference=record.payment_reference,
    )


def _load_record(db: DbSession, record_id: int) -> PayrollRecord:
    record = db.scalar(
        select(PayrollRecord).options(*_RECORD_LOAD, selectinload(PayrollRecord.line_items))
        .where(PayrollRecord.id == record_id).execution_options(populate_existing=True)
    )
    if record is None:
        raise _error(status.HTTP_404_NOT_FOUND, RECORD_NOT_FOUND)
    return record


@router.get("/periods/{period_id}/records", response_model=Page[PayrollRecordSummary])
def list_period_records(period_id: int, params: Annotated[RecordListParams, Query()], db: DbSession, _: Admin) -> Page[PayrollRecordSummary]:
    _get_period(db, period_id)
    stmt = (select(PayrollRecord).options(*_RECORD_LOAD).join(Employee, Employee.id == PayrollRecord.employee_id)
            .where(PayrollRecord.period_id == period_id).order_by(Employee.employee_code, PayrollRecord.id))
    if params.status is not None:
        stmt = stmt.where(PayrollRecord.status == params.status)
    if params.department_id is not None:
        stmt = stmt.where(Employee.department_id == params.department_id)
    if params.employee_id is not None:
        stmt = stmt.where(PayrollRecord.employee_id == params.employee_id)
    items, total = paginate(db, stmt, params)
    return Page[PayrollRecordSummary].build([PayrollRecordSummary(**_summary(r)) for r in items], total, params)


@router.get("/records/{record_id}", response_model=PayrollRecordRead)
def get_record(record_id: int, db: DbSession, _: Admin) -> PayrollRecordRead:
    return _record_read(_load_record(db, record_id))


@router.post("/records/{record_id}/approve", response_model=PayrollRecordRead)
def approve_record(record_id: int, db: DbSession, actor: Admin) -> PayrollRecordRead:
    record = _load_record(db, record_id)
    _forbid_self(db, actor, record.employee_id, "approve")
    if record.status != PayrollStatus.CALCULATED:
        raise _error(status.HTTP_409_CONFLICT, f"Only CALCULATED payroll can be approved (current: {record.status.value})")
    if not payroll_service.transition(db, record.id, PayrollStatus.CALCULATED,
                                      {"status": PayrollStatus.APPROVED, "approved_at": utcnow(),
                                       "approved_by_user_id": actor.id}):
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "This payroll record was already processed")
    record_audit(db, actor=actor, action="payroll.record.approved", entity_type="payroll_record", entity_id=record.id,
                 details={"employee_id": record.employee_id, "period_id": record.period_id})
    if record.employee.user_id is not None:
        notify(db, user_id=record.employee.user_id, category="payroll", title="Payslip available",
               message=f"Your payslip for {record.period.year}-{record.period.month:02d} is available.",
               entity_type="payroll_record", entity_id=record.id)
    _commit(db, "This payroll record was already processed")
    return _record_read(_load_record(db, record.id))


@router.post("/records/{record_id}/mark-paid", response_model=PayrollRecordRead)
def mark_record_paid(record_id: int, payload: MarkPaidRequest, db: DbSession, actor: Admin) -> PayrollRecordRead:
    """Administrative status change only. It does not move money or contact any bank."""
    record = _load_record(db, record_id)
    _forbid_self(db, actor, record.employee_id, "mark as paid")
    if record.status != PayrollStatus.APPROVED:
        raise _error(status.HTTP_409_CONFLICT, f"Only APPROVED payroll can be marked paid (current: {record.status.value})")
    if not payroll_service.transition(db, record.id, PayrollStatus.APPROVED,
                                      {"status": PayrollStatus.PAID, "paid_at": utcnow(), "paid_by_user_id": actor.id,
                                       "payment_reference": payload.payment_reference}):
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "This payroll record was already processed")
    record_audit(db, actor=actor, action="payroll.record.paid", entity_type="payroll_record", entity_id=record.id,
                 details={"employee_id": record.employee_id, "period_id": record.period_id,
                          "has_payment_reference": payload.payment_reference is not None})
    _commit(db, "This payroll record was already processed")
    return _record_read(_load_record(db, record.id))


# --- Employee self-service ---------------------------------------------------------------------


def _own_employee_or_404(db: DbSession, user: User) -> Employee:
    employee = _own_employee(db, user)
    if employee is None:
        raise _error(status.HTTP_404_NOT_FOUND, "No employee profile is linked to your account")
    return employee


@router.get("/me", response_model=Page[PayrollRecordSummary])
def my_payroll(params: Annotated[MyPayrollParams, Query()], db: DbSession, user: SelfService) -> Page[PayrollRecordSummary]:
    """Your APPROVED and PAID payroll only; CALCULATED records are still under review."""
    employee = _own_employee_or_404(db, user)
    stmt = (select(PayrollRecord).options(*_RECORD_LOAD).join(PayrollPeriod, PayrollPeriod.id == PayrollRecord.period_id)
            .where(PayrollRecord.employee_id == employee.id, PayrollRecord.status.in_(EMPLOYEE_VISIBLE))
            .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc()))
    if params.year is not None:
        stmt = stmt.where(PayrollPeriod.year == params.year)
    items, total = paginate(db, stmt, params)
    return Page[PayrollRecordSummary].build([PayrollRecordSummary(**_summary(r)) for r in items], total, params)


@router.get("/me/{record_id}/payslip", response_model=Payslip)
def my_payslip(record_id: int, db: DbSession, user: SelfService) -> Payslip:
    """Built only from your own persisted APPROVED/PAID record; anything else is 404."""
    employee = _own_employee_or_404(db, user)
    record = db.scalar(
        select(PayrollRecord).options(*_RECORD_LOAD, selectinload(PayrollRecord.line_items))
        .where(PayrollRecord.id == record_id, PayrollRecord.employee_id == employee.id,
               PayrollRecord.status.in_(EMPLOYEE_VISIBLE))
    )
    if record is None:
        raise _error(status.HTTP_404_NOT_FOUND, RECORD_NOT_FOUND)
    department = db.get(Department, record.employee.department_id)
    earnings, deductions = _split_lines(record)
    return Payslip(
        record_id=record.id,
        employee=PayslipEmployee(employee_code=record.employee.employee_code, full_name=record.employee.full_name,
                                 department=department.name if department else None,
                                 designation=record.employee.designation),
        period=PeriodRef.model_validate(record.period), currency=record.currency,
        earnings=earnings, deductions=deductions, gross_earnings=record.gross_earnings,
        total_deductions=record.total_deductions, net_pay=record.net_pay, status=record.status,
        generated_at=utcnow(),
    )
