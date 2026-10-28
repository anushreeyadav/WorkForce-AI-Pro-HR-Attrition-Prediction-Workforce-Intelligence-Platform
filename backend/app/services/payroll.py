"""Payroll orchestration: profile selection, attendance/leave facts, and period calculation.

Reads attendance and leave but never creates, edits or deletes them. All writes go into the caller's
session; the router commits once, together with the audit row.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.models import (
    Attendance,
    AttendanceStatus,
    Employee,
    EmploymentStatus,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
    PayrollLineItem,
    PayrollPeriod,
    PayrollProfile,
    PayrollProfileComponent,
    PayrollRecord,
    PayrollStatus,
    User,
)
from app.services import payroll_engine as engine
from app.services.leave_attendance import conflicts_query
from app.services.leave_calendar import count_leave_days


class PayrollConflictError(Exception):
    """Payroll data changed concurrently; nothing from this calculation should be committed."""


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def overlapping_profiles(
    db: Session, employee_id: int, start: date, end: date | None, exclude_id: int | None = None
) -> list[PayrollProfile]:
    """Active profiles of the employee overlapping [start, end]; end None means open-ended."""
    stmt = (
        select(PayrollProfile)
        .options(selectinload(PayrollProfile.components).selectinload(PayrollProfileComponent.component))
        .where(
            PayrollProfile.employee_id == employee_id,
            PayrollProfile.is_active.is_(True),
            (PayrollProfile.effective_to.is_(None)) | (PayrollProfile.effective_to >= start),
        )
        .order_by(PayrollProfile.effective_from, PayrollProfile.id)
    )
    if end is not None:
        stmt = stmt.where(PayrollProfile.effective_from <= end)
    if exclude_id is not None:
        stmt = stmt.where(PayrollProfile.id != exclude_id)
    return list(db.scalars(stmt).all())


@dataclass
class EmployeeFacts:
    working_days: int
    day_deductions: list[engine.DayDeduction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Employee ids per IN (...) list when payroll inputs are read for many employees at once.
FACTS_CHUNK = 500


def _chunks(ids: list[int]):
    for start in range(0, len(ids), FACTS_CHUNK):
        yield ids[start:start + FACTS_CHUNK]


def profiles_by_employee(db: Session, employee_ids: list[int], start: date, end: date) -> dict[int, list[PayrollProfile]]:
    """overlapping_profiles() for many employees with one query per chunk (components loaded alongside)."""
    found: dict[int, list[PayrollProfile]] = {i: [] for i in employee_ids}
    for chunk in _chunks(employee_ids):
        for profile in db.scalars(
            select(PayrollProfile)
            .options(selectinload(PayrollProfile.components).selectinload(PayrollProfileComponent.component))
            .where(PayrollProfile.employee_id.in_(chunk), PayrollProfile.is_active.is_(True),
                   (PayrollProfile.effective_to.is_(None)) | (PayrollProfile.effective_to >= start),
                   PayrollProfile.effective_from <= end)
            .order_by(PayrollProfile.employee_id, PayrollProfile.effective_from, PayrollProfile.id)
        ):
            found[profile.employee_id].append(profile)
    return found


@dataclass
class PeriodFacts:
    """Attendance and leave inputs of one period for a set of employees, read with grouped queries."""

    unpaid_leave: dict[int, list[LeaveRequest]] = field(default_factory=dict)
    absent_days: dict[int, int] = field(default_factory=dict)
    open_records: dict[int, int] = field(default_factory=dict)
    leave_conflicts: dict[int, int] = field(default_factory=dict)


def prefetch_facts(db: Session, employee_ids: list[int], period: PayrollPeriod) -> PeriodFacts:
    """Four grouped queries per chunk of employees instead of four queries per employee. Same filters as before."""
    start, end = period.start_date, period.end_date
    facts = PeriodFacts()
    for chunk in _chunks(employee_ids):
        for request in db.scalars(
            select(LeaveRequest)
            .options(selectinload(LeaveRequest.policy))
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .where(LeaveRequest.employee_id.in_(chunk), LeaveRequest.status == LeaveRequestStatus.APPROVED,
                   LeaveType.is_paid.is_(False), LeaveRequest.start_date <= end, LeaveRequest.end_date >= start)
            .order_by(LeaveRequest.employee_id, LeaveRequest.id)
        ):
            facts.unpaid_leave.setdefault(request.employee_id, []).append(request)
        in_period = (Attendance.employee_id.in_(chunk), Attendance.work_date >= start, Attendance.work_date <= end)
        facts.absent_days.update(db.execute(
            select(Attendance.employee_id, func.count(Attendance.id))
            .where(*in_period, Attendance.status == AttendanceStatus.ABSENT).group_by(Attendance.employee_id)).all())
        facts.open_records.update(db.execute(
            select(Attendance.employee_id, func.count(Attendance.id))
            .where(*in_period, Attendance.check_in.is_not(None), Attendance.check_out.is_(None))
            .group_by(Attendance.employee_id)).all())
        pairs = conflicts_query().where(*in_period).subquery()  # one row per (attendance, approved leave) pair
        facts.leave_conflicts.update(db.execute(
            select(pairs.c.employee_id, func.count()).group_by(pairs.c.employee_id)).all())
    return facts


def gather_facts(db: Session, employee: Employee, profile: PayrollProfile, period: PayrollPeriod, settings: Settings,
                 prefetched: PeriodFacts | None = None) -> EmployeeFacts:
    """Payroll inputs for one employee. calculate_period passes facts prefetched for the whole batch."""
    if prefetched is None:
        prefetched = prefetch_facts(db, [employee.id], period)
    start, end = period.start_date, period.end_date
    facts = EmployeeFacts(working_days=count_leave_days(start, end, settings.leave_weekend_days, False))

    if start < employee.date_of_joining <= end:
        facts.warnings.append(f"Joined on {employee.date_of_joining.isoformat()}; no proration applied")
    if profile.effective_from > start:
        facts.warnings.append(f"Salary profile starts {profile.effective_from.isoformat()}; no proration applied")
    if profile.effective_to is not None and profile.effective_to < end:
        facts.warnings.append(f"Salary profile ends {profile.effective_to.isoformat()}; no proration applied")

    # Unpaid leave: APPROVED requests of leave types marked unpaid, clipped to the period.
    unpaid_days = 0
    for request in prefetched.unpaid_leave.get(employee.id, []):
        unpaid_days += count_leave_days(max(request.start_date, start), min(request.end_date, end),
                                        settings.leave_weekend_days, request.policy.count_weekends)
    if unpaid_days:
        if settings.payroll_deduct_unpaid_leave:
            facts.day_deductions.append(engine.DayDeduction("UNPAID_LEAVE", "Unpaid leave", Decimal(unpaid_days)))
        else:
            facts.warnings.append(f"{unpaid_days} day(s) of approved unpaid leave not deducted (rule disabled)")

    absent_days = prefetched.absent_days.get(employee.id, 0)
    if absent_days:
        if settings.payroll_deduct_recorded_absence:
            facts.day_deductions.append(engine.DayDeduction("ABSENCE", "Recorded absence", Decimal(absent_days)))
        else:
            facts.warnings.append(f"{absent_days} recorded ABSENT day(s) not deducted (rule disabled)")

    open_records = prefetched.open_records.get(employee.id, 0)
    if open_records:
        facts.warnings.append(f"{open_records} attendance record(s) without check-out")

    conflicts = prefetched.leave_conflicts.get(employee.id, 0)
    if conflicts:
        facts.warnings.append(f"{conflicts} day(s) with attendance recorded during approved leave")
    return facts


def component_inputs(profile: PayrollProfile, warnings: list[str]) -> list[engine.ComponentInput]:
    inputs = []
    for item in profile.components:
        component = item.component
        if not component.is_active:
            warnings.append(f"Inactive component {component.code} ignored")
            continue
        value = item.amount if item.amount is not None else item.percentage
        inputs.append(engine.ComponentInput(component.code, component.name, component.kind,
                                            component.calculation_method, value, component.display_order))
    return inputs


@dataclass
class CalculationSummary:
    created: list[int] = field(default_factory=list)
    recalculated: list[int] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def _lines(result: engine.PayrollResult) -> list[PayrollLineItem]:
    return [
        PayrollLineItem(sequence=index, kind=line.kind, code=line.code, name=line.name,
                        calculation_method=line.method, rate=line.rate, basis=line.basis,
                        quantity=line.quantity, amount=line.amount, detail=line.detail)
        for index, line in enumerate(result.lines, start=1)
    ]


def calculate_period(
    db: Session, period: PayrollPeriod, actor: User, settings: Settings, now: datetime,
    employee_ids: list[int] | None = None,
) -> CalculationSummary:
    # Serialize calculations of the same period (row lock on MySQL; SQLite serializes writes).
    db.execute(select(PayrollPeriod.id).where(PayrollPeriod.id == period.id).with_for_update())

    profiled = select(PayrollProfile.employee_id).where(
        PayrollProfile.is_active.is_(True),
        PayrollProfile.effective_from <= period.end_date,
        (PayrollProfile.effective_to.is_(None)) | (PayrollProfile.effective_to >= period.start_date),
    )
    stmt = select(Employee).order_by(Employee.id)
    stmt = stmt.where(Employee.id.in_(employee_ids)) if employee_ids else stmt.where(Employee.id.in_(profiled))
    employees = list(db.scalars(stmt).all())

    summary = CalculationSummary()
    if employee_ids:
        found = {e.id for e in employees}
        summary.skipped += [{"employee_id": i, "reason": "Employee not found"} for i in sorted(set(employee_ids) - found)]

    existing = {
        r.employee_id: r for r in db.scalars(
            select(PayrollRecord).where(PayrollRecord.period_id == period.id)
            .execution_options(populate_existing=True)
        )
    }

    # Inputs for every employee are read up front with grouped queries (not ~7 queries per employee).
    ids = [e.id for e in employees]
    profiles_of = profiles_by_employee(db, ids, period.start_date, period.end_date)
    period_facts = prefetch_facts(db, ids, period)

    for employee in employees:
        record = existing.get(employee.id)
        if record is not None and record.status != PayrollStatus.CALCULATED:
            summary.skipped.append({"employee_id": employee.id, "reason": f"Payroll already {record.status.value.lower()}"})
            continue
        if employee.status == EmploymentStatus.INACTIVE:
            summary.skipped.append({"employee_id": employee.id, "reason": "Employee is inactive"})
            continue
        profiles = profiles_of[employee.id]
        if not profiles:
            summary.skipped.append({"employee_id": employee.id, "reason": "No active salary profile for this period"})
            continue
        if len(profiles) > 1:
            summary.skipped.append({"employee_id": employee.id,
                                    "reason": "Multiple salary profiles in this period; proration is not supported"})
            continue

        profile = profiles[0]
        facts = gather_facts(db, employee, profile, period, settings, prefetched=period_facts)
        try:
            result = engine.calculate(profile.base_salary, component_inputs(profile, facts.warnings),
                                      working_days=facts.working_days, day_deductions=facts.day_deductions)
        except engine.PayrollCalculationError as exc:
            summary.skipped.append({"employee_id": employee.id, "reason": str(exc)})
            continue

        values = dict(
            profile_id=profile.id, currency=settings.payroll_currency, base_salary=engine.money(profile.base_salary),
            gross_earnings=result.gross_earnings, total_deductions=result.total_deductions, net_pay=result.net_pay,
            warnings=facts.warnings or None, calculated_at=now, calculated_by_user_id=actor.id,
        )
        if record is None:
            record = PayrollRecord(period_id=period.id, employee_id=employee.id, status=PayrollStatus.CALCULATED,
                                   **values)
            record.line_items = _lines(result)
            db.add(record)
            db.flush()
            summary.created.append(record.id)
        else:
            # Conditional: only a record that is still CALCULATED may be replaced.
            changed = db.execute(
                update(PayrollRecord)
                .where(PayrollRecord.id == record.id, PayrollRecord.status == PayrollStatus.CALCULATED)
                .values(**values)
                .execution_options(synchronize_session=False)
            ).rowcount
            if changed != 1:
                raise PayrollConflictError(f"Payroll record {record.id} changed during calculation")
            db.execute(delete(PayrollLineItem).where(PayrollLineItem.record_id == record.id))
            for item in _lines(result):
                item.record_id = record.id
                db.add(item)
            db.flush()
            summary.recalculated.append(record.id)
    return summary


def transition(db: Session, record_id: int, from_status: PayrollStatus, values: dict) -> bool:
    """Atomic status change; False if the record is no longer in from_status."""
    return db.execute(
        update(PayrollRecord)
        .where(PayrollRecord.id == record_id, PayrollRecord.status == from_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    ).rowcount == 1
