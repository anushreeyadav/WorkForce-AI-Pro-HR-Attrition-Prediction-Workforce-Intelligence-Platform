"""Report data: rows for each report, built from the same queries and services as the corresponding APIs.

Every function takes an already-resolved department scope (None = organization; managers are pinned to their own
department by the router) and a row limit. It fetches at most limit+1 rows and raises TooManyRows rather than
silently truncating. Columns are an explicit allow-list: no passwords, tokens, emails, phone numbers, notes,
reasons, review comments, model features or individual risk scores ever appear.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Attendance,
    Department,
    Employee,
    LeaveRequest,
    LeaveType,
    PayrollPeriod,
    PayrollRecord,
)
from app.services import activity_feed as feed
from app.services import workforce_analytics as wa
from app.services.report_export import Column


class TooManyRows(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.limit = limit


@dataclass
class ReportData:
    columns: list[Column]
    rows: list[dict]


def _bounded(db: Session, stmt, limit: int) -> list:
    rows = db.execute(stmt.limit(limit + 1)).all()
    if len(rows) > limit:
        raise TooManyRows(limit)
    return rows


def _department(stmt, department_id: int | None):
    return stmt if department_id is None else stmt.where(Employee.department_id == department_id)


# --- Employees ------------------------------------------------------------------------------------------

EMPLOYEE_COLUMNS = [Column("employee_code", "Employee code"), Column("full_name", "Full name"),
                    Column("department", "Department"), Column("designation", "Designation"),
                    Column("status", "Employment status"), Column("date_of_joining", "Date of joining", "date")]


def employees(db: Session, department_id: int | None, status, limit: int) -> ReportData:
    stmt = (select(Employee.employee_code, Employee.full_name, Department.name, Employee.designation, Employee.status,
                   Employee.date_of_joining).join(Department, Department.id == Employee.department_id))
    stmt = _department(stmt, department_id)
    if status is not None:
        stmt = stmt.where(Employee.status == status)
    rows = _bounded(db, stmt.order_by(Employee.employee_code, Employee.id), limit)
    return ReportData(EMPLOYEE_COLUMNS, [
        {"employee_code": code, "full_name": name, "department": dept, "designation": designation,
         "status": emp_status.value, "date_of_joining": joined}
        for code, name, dept, designation, emp_status, joined in rows])


# --- Attendance (same filters and order as GET /api/attendance) --------------------------------------------

ATTENDANCE_COLUMNS = [Column("employee_code", "Employee code"), Column("full_name", "Full name"),
                      Column("department", "Department"), Column("work_date", "Work date", "date"),
                      Column("status", "Status"), Column("check_in", "Check-in", "datetime"),
                      Column("check_out", "Check-out", "datetime"), Column("break_minutes", "Break minutes", "int"),
                      Column("work_minutes", "Work minutes", "int"), Column("overtime_minutes", "Overtime minutes", "int")]


def attendance(db: Session, department_id: int | None, date_from: date, date_to: date, status, limit: int) -> ReportData:
    stmt = (select(Employee.employee_code, Employee.full_name, Department.name, Attendance.work_date, Attendance.status,
                   Attendance.check_in, Attendance.check_out, Attendance.break_minutes, Attendance.work_minutes,
                   Attendance.overtime_minutes)
            .join(Employee, Employee.id == Attendance.employee_id).join(Department, Department.id == Employee.department_id)
            .where(Attendance.work_date >= date_from, Attendance.work_date <= date_to))
    stmt = _department(stmt, department_id)
    if status is not None:
        stmt = stmt.where(Attendance.status == status)
    rows = _bounded(db, stmt.order_by(Attendance.work_date.desc(), Attendance.id.desc()), limit)
    return ReportData(ATTENDANCE_COLUMNS, [
        {"employee_code": r[0], "full_name": r[1], "department": r[2], "work_date": r[3], "status": r[4].value,
         "check_in": r[5], "check_out": r[6], "break_minutes": r[7], "work_minutes": r[8], "overtime_minutes": r[9]}
        for r in rows])


# --- Leave (same overlap window and order as GET /api/leave/requests) ----------------------------------------

LEAVE_COLUMNS = [Column("employee_code", "Employee code"), Column("full_name", "Full name"),
                 Column("department", "Department"), Column("leave_type", "Leave type"),
                 Column("start_date", "Start date", "date"), Column("end_date", "End date", "date"),
                 Column("working_days", "Working days", "int"), Column("status", "Status"),
                 Column("submitted_at", "Submitted at", "datetime"), Column("reviewed_at", "Reviewed at", "datetime"),
                 Column("cancelled_at", "Cancelled at", "datetime")]


def leave(db: Session, department_id: int | None, date_from: date, date_to: date, status, limit: int) -> ReportData:
    stmt = (select(Employee.employee_code, Employee.full_name, Department.name, LeaveType.code, LeaveRequest.start_date,
                   LeaveRequest.end_date, LeaveRequest.working_days, LeaveRequest.status, LeaveRequest.created_at,
                   LeaveRequest.reviewed_at, LeaveRequest.cancelled_at)
            .join(Employee, Employee.id == LeaveRequest.employee_id).join(Department, Department.id == Employee.department_id)
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .where(LeaveRequest.end_date >= date_from, LeaveRequest.start_date <= date_to))
    stmt = _department(stmt, department_id)
    if status is not None:
        stmt = stmt.where(LeaveRequest.status == status)
    rows = _bounded(db, stmt.order_by(LeaveRequest.start_date.desc(), LeaveRequest.id.desc()), limit)
    return ReportData(LEAVE_COLUMNS, [
        {"employee_code": r[0], "full_name": r[1], "department": r[2], "leave_type": r[3], "start_date": r[4],
         "end_date": r[5], "working_days": r[6], "status": r[7].value, "submitted_at": r[8], "reviewed_at": r[9],
         "cancelled_at": r[10]} for r in rows])


# --- Payroll (same rows and order as GET /api/payroll/periods/{id}/records) -----------------------------------

PAYROLL_COLUMNS = [Column("employee_code", "Employee code"), Column("full_name", "Full name"),
                   Column("department", "Department"), Column("period", "Period"), Column("status", "Status"),
                   Column("currency", "Currency"), Column("gross_earnings", "Gross earnings", "decimal"),
                   Column("total_deductions", "Total deductions", "decimal"), Column("net_pay", "Net pay", "decimal")]


def payroll(db: Session, period: PayrollPeriod, department_id: int | None, status, limit: int) -> ReportData:
    stmt = (select(Employee.employee_code, Employee.full_name, Department.name, PayrollRecord.status, PayrollRecord.currency,
                   PayrollRecord.gross_earnings, PayrollRecord.total_deductions, PayrollRecord.net_pay)
            .join(Employee, Employee.id == PayrollRecord.employee_id).join(Department, Department.id == Employee.department_id)
            .where(PayrollRecord.period_id == period.id))
    stmt = _department(stmt, department_id)
    if status is not None:
        stmt = stmt.where(PayrollRecord.status == status)
    rows = _bounded(db, stmt.order_by(Employee.employee_code, PayrollRecord.id), limit)
    label = f"{period.year}-{period.month:02d}"
    return ReportData(PAYROLL_COLUMNS, [
        {"employee_code": r[0], "full_name": r[1], "department": r[2], "period": label, "status": r[3].value,
         "currency": r[4], "gross_earnings": r[5], "total_deductions": r[6], "net_pay": r[7]} for r in rows])


# --- Attrition (aggregate AI risk indicators, never individuals) ----------------------------------------------

ATTRITION_COLUMNS = [Column("department", "Department"), Column("current_employees", "Current employees", "int"),
                     Column("with_prediction", "With compatible prediction", "int"),
                     Column("low", "Latest estimate LOW", "int"), Column("medium", "Latest estimate MEDIUM", "int"),
                     Column("high", "Latest estimate HIGH", "int"), Column("without_prediction", "Without prediction", "int"),
                     Column("incompatible_only", "Incompatible prediction only", "int"),
                     Column("alerts_open", "Alerts open", "int"), Column("alerts_acknowledged", "Alerts acknowledged", "int"),
                     Column("alerts_resolved", "Alerts resolved", "int"), Column("alerts_dismissed", "Alerts dismissed", "int")]


def attrition(db: Session, department_id: int | None, exclude_user_id: int, limit: int) -> ReportData:
    """One row per department in scope, plus an organization row for organization scope. Uses the same
    calculation as the workforce-analytics AI risk indicators (the caller's own record excluded)."""
    if department_id is None:
        departments = _bounded(db, select(Department.id, Department.name).order_by(Department.name, Department.id), limit - 1)
        scopes = [(d_id, name) for d_id, name in departments] + [(None, "All departments")]
    else:
        scopes = [(department_id, db.scalar(select(Department.name).where(Department.id == department_id)))]
    rows = []
    for d_id, name in scopes:
        data = wa.risk_indicators(db, d_id, exclude_user_id)
        by_category, alerts = data["latest_prediction_by_category"], data["risk_alerts_by_status"]
        rows.append({"department": name, "current_employees": data["current_employees_in_scope"],
                     "with_prediction": data["employees_with_compatible_prediction"],
                     "low": by_category["LOW"], "medium": by_category["MEDIUM"], "high": by_category["HIGH"],
                     "without_prediction": data["employees_without_prediction"],
                     "incompatible_only": data["employees_with_incompatible_prediction_only"],
                     "alerts_open": alerts["OPEN"], "alerts_acknowledged": alerts["ACKNOWLEDGED"],
                     "alerts_resolved": alerts["RESOLVED"], "alerts_dismissed": alerts["DISMISSED"]})
    return ReportData(ATTRITION_COLUMNS, rows)


# --- Workforce trend (monthly rows exactly as GET /api/workforce-analytics/trends) ------------------------------

WORKFORCE_COLUMNS = [Column("month", "Month"), Column("observed", "Snapshot observed", "bool"),
                     Column("snapshot_date", "Snapshot date", "date"), Column("total_employees", "Total employees", "int"),
                     Column("active_employees", "Active", "int"), Column("on_leave_employees", "On leave", "int"),
                     Column("inactive_employees", "Inactive", "int"), Column("joiners", "Joiners", "int"),
                     Column("recorded_departures", "Recorded departures", "int"),
                     Column("turnover_rate", "Turnover rate", "decimal")]


def workforce(monthly_rows: list[dict]) -> ReportData:
    rows = [{**row, "snapshot_date": date.fromisoformat(row["snapshot_date"]) if row["snapshot_date"] else None}
            for row in monthly_rows]
    return ReportData(WORKFORCE_COLUMNS, rows)


# --- Activity (feed items exactly as GET /api/workforce-activity/feed) -----------------------------------------

ACTIVITY_COLUMNS = [Column("recorded_at", "Recorded at", "datetime"), Column("source_event_at", "Source event at"),
                    Column("category", "Category"), Column("kind", "Kind"), Column("action", "Action"),
                    Column("description", "Description"), Column("department_id", "Department ID", "int"),
                    Column("employee_id", "Employee ID", "int"), Column("priority", "Priority"), Column("event_id", "Event ID")]


def activity(db: Session, scope: feed.Scope, start: datetime, end: datetime, categories: set[str], kinds: set[str] | None,
             limit: int) -> ReportData:
    result = feed.collect(db, scope, start, end, categories, kinds, None, limit)
    if result.truncated:
        raise TooManyRows(limit)
    rows = []
    for item in result.items:
        when = item.source_event_at
        rows.append({"recorded_at": item.recorded_at, "category": item.category, "kind": item.kind, "action": item.action,
                     "source_event_at": when.isoformat() if when is not None else None, "description": item.description,
                     "department_id": item.department_id, "employee_id": item.employee_id, "priority": item.priority,
                     "event_id": item.event_id})
    return ReportData(ACTIVITY_COLUMNS, rows)
