"""Workforce reports as CSV, Excel (XLSX) or PDF downloads.

Each report uses the permission and department-scope rules of the module it comes from (see
app/core/permissions.REPORTS); MANAGER is pinned to their own department and other departments return 404.
Reports are bounded: more rows than the limit is a 422 asking for narrower filters, never a silent cut.
Only payroll exports are audited (bulk salary data, like the audited dataset download); the audit row holds the
period, scope, format and row count, never any values. All other reports are read-only.
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import CurrentUser, DbSession, require_roles
from app.api.workforce_activity import Category, Kind
from app.api.workforce_analytics import MAX_TREND_MONTHS, _month_rows, resolve_department
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    AttendanceStatus,
    Department,
    EmploymentStatus,
    LeaveRequestStatus,
    PayrollPeriod,
    PayrollStatus,
    User,
    UserRole,
)
from app.models.base import utcnow
from app.services import activity_feed as feed
from app.services import report_export as export
from app.services import reports as data
from app.services import workforce_math as wm
from app.services.audit import record_audit

router = APIRouter(prefix="/api/reports", tags=["reports"])

Format = Literal["csv", "xlsx", "pdf"]
PositiveId = Annotated[int, Field(gt=0)]
MEDIA_TYPES = {"csv": "text/csv; charset=utf-8",
               "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               "pdf": "application/pdf"}
CONFIDENTIAL = "Confidential: internal HR use only."


def _reader(report: str):
    return Annotated[User, Depends(require_roles(*permissions.REPORTS[report]))]


CATALOG = {
    "employees": ("Employee directory", "Employee code, name, department, designation, status and joining date. "
                  "No contact details.", ["department_id", "status"]),
    "attendance": ("Attendance records", "Daily attendance records with check-in/out times and minutes. No notes.",
                   ["date_from", "date_to", "department_id", "status"]),
    "leave": ("Leave requests", "Leave requests overlapping the date range, with status and review times. "
              "No reasons or review comments.", ["date_from", "date_to", "department_id", "status"]),
    "payroll": ("Payroll register", "Gross, deductions and net pay per employee for one payroll period. "
                "Audited export.", ["period_id", "department_id", "status"]),
    "attrition": ("AI risk indicators (aggregate)", "Counts of the latest stored risk estimate category and alert "
                  "status per department. No individual scores or model features; your own record is excluded.",
                  ["department_id"]),
    "workforce": ("Workforce trend", "Monthly snapshot headcount, joiners, recorded departures and turnover.",
                  ["date_from", "date_to", "department_id"]),
    "activity": ("Workforce activity", "Activity feed items for a date window (same content as the activity feed).",
                 ["date_from", "date_to", "department_id", "category", "kind"]),
}


# --- Parameters -------------------------------------------------------------------------------------------


class ReportParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Format = "csv"
    department_id: PositiveId | None = None


class DatedParams(ReportParams):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _order(self) -> "DatedParams":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class EmployeeReportParams(ReportParams):
    status: EmploymentStatus | None = None


class AttendanceReportParams(DatedParams):
    status: AttendanceStatus | None = None


class LeaveReportParams(DatedParams):
    status: LeaveRequestStatus | None = None


class PayrollReportParams(ReportParams):
    period_id: PositiveId
    status: PayrollStatus | None = None


class ActivityReportParams(DatedParams):
    category: list[Category] | None = None
    kind: list[Kind] | None = None


# --- Helpers ---------------------------------------------------------------------------------------------


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _today() -> date:
    return utcnow().astimezone(get_settings().org_tz).date()


def _days(params: DatedParams, default_days: int, max_days: int) -> tuple[date, date]:
    end = params.date_to or _today()
    start = params.date_from or end - timedelta(days=default_days - 1)
    if start > end:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be on or before date_to")
    if (end - start).days + 1 > max_days:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The date range may cover at most {max_days} days")
    return start, end


def _limit(fmt: str) -> int:
    settings = get_settings()
    return settings.report_pdf_max_rows if fmt == "pdf" else settings.report_max_rows


def _safe_filename(*parts: str) -> str:
    name = "_".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:120] or "report"


def _scope_label(db: DbSession, department_id: int | None) -> tuple[str, str]:
    if department_id is None:
        return "organization", "Scope: organization"
    name = db.get(Department, department_id).name
    return f"department-{department_id}", f"Scope: department {name}"


def render_report(report: str, title: str, fmt: str, rows: data.ReportData, filename_parts: list[str], subtitle: list[str]) -> Response:
    settings = get_settings()
    generated = utcnow().astimezone(settings.org_tz)
    table = export.Table(title=title, columns=rows.columns, rows=rows.rows, timezone=settings.org_tz,
                         subtitle=[*subtitle, f"Generated {generated.isoformat(timespec='seconds')} "
                                   f"(times in {settings.org_timezone}). {CONFIDENTIAL}"])
    if fmt == "csv":
        content = export.to_csv(table)
    elif fmt == "xlsx":
        content = export.to_xlsx(table)
    else:
        content = export.to_pdf(table, footer=f"{title}. {CONFIDENTIAL}")
    filename = _safe_filename(report, *filename_parts, generated.strftime("%Y%m%d-%H%M")) + f".{fmt}"
    return Response(content=content, media_type=MEDIA_TYPES[fmt], headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff", "X-Report-Rows": str(len(rows.rows))})


def _rows_or_422(build) -> data.ReportData:
    try:
        return build()
    except data.TooManyRows as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"The report has more than {exc.limit} rows for this format; narrow the filters") from None


# --- Catalog ---------------------------------------------------------------------------------------------


@router.get("/catalog")
def catalog(user: CurrentUser) -> dict:
    """Reports the caller may run, with formats, filters and limits. Contains no employee or department data."""
    settings = get_settings()
    available = [{"id": report, "title": title, "description": description, "path": f"/api/reports/{report}",
                  "formats": list(MEDIA_TYPES), "filters": ["format", *filters],
                  "roles": [r.value for r in permissions.REPORTS[report]],
                  "scope": ("organization or one department" if user.role in (UserRole.ADMIN, UserRole.HR)
                            else "your department only")}
                 for report, (title, description, filters) in CATALOG.items()
                 if user.role in permissions.REPORTS[report]]
    return {"reports": available,
            "limits": {"max_rows": settings.report_max_rows, "pdf_max_rows": settings.report_pdf_max_rows,
                       "max_range_days": settings.report_max_range_days},
            "note": "Reports larger than the limit are refused (422); narrow the filters instead."}


# --- Reports ---------------------------------------------------------------------------------------------


@router.get("/employees")
def employees_report(params: Annotated[EmployeeReportParams, Query()], db: DbSession, user: _reader("employees")) -> Response:
    department_id = resolve_department(db, user, params.department_id)
    rows = _rows_or_422(lambda: data.employees(db, department_id, params.status, _limit(params.format)))
    slug, label = _scope_label(db, department_id)
    filters = [label] + ([f"Status: {params.status.value}"] if params.status else [])
    return render_report("employees", "Employee directory", params.format, rows, [slug], filters)


@router.get("/attendance")
def attendance_report(params: Annotated[AttendanceReportParams, Query()], db: DbSession, user: _reader("attendance")) -> Response:
    department_id = resolve_department(db, user, params.department_id)
    start, end = _days(params, 30, get_settings().report_max_range_days)
    rows = _rows_or_422(lambda: data.attendance(db, department_id, start, end, params.status, _limit(params.format)))
    slug, label = _scope_label(db, department_id)
    filters = [label, f"Work dates {start.isoformat()} to {end.isoformat()}"] + (
        [f"Status: {params.status.value}"] if params.status else [])
    return render_report("attendance", "Attendance records", params.format, rows, [slug, f"{start}_{end}"], filters)


@router.get("/leave")
def leave_report(params: Annotated[LeaveReportParams, Query()], db: DbSession, user: _reader("leave")) -> Response:
    department_id = resolve_department(db, user, params.department_id)
    start, end = _days(params, 30, get_settings().report_max_range_days)
    rows = _rows_or_422(lambda: data.leave(db, department_id, start, end, params.status, _limit(params.format)))
    slug, label = _scope_label(db, department_id)
    filters = [label, f"Requests overlapping {start.isoformat()} to {end.isoformat()}"] + (
        [f"Status: {params.status.value}"] if params.status else [])
    return render_report("leave", "Leave requests", params.format, rows, [slug, f"{start}_{end}"], filters)


@router.get("/payroll")
def payroll_report(params: Annotated[PayrollReportParams, Query()], db: DbSession, user: _reader("payroll")) -> Response:
    department_id = resolve_department(db, user, params.department_id)
    period = db.get(PayrollPeriod, params.period_id)
    if period is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Payroll period not found")
    rows = _rows_or_422(lambda: data.payroll(db, period, department_id, params.status, _limit(params.format)))
    slug, label = _scope_label(db, department_id)
    response = render_report("payroll", f"Payroll register {period.year}-{period.month:02d}", params.format, rows,
                       [slug, f"{period.year}-{period.month:02d}"],
                       [label] + ([f"Status: {params.status.value}"] if params.status else []))
    record_audit(db, actor=user, action="payroll.report_exported", entity_type="payroll_period", entity_id=period.id,
                 details={"format": params.format, "rows": len(rows.rows), "department_id": department_id,
                          "status": params.status.value if params.status else None})
    db.commit()
    return response


@router.get("/attrition")
def attrition_report(params: Annotated[ReportParams, Query()], db: DbSession, user: _reader("attrition")) -> Response:
    department_id = resolve_department(db, user, params.department_id)
    rows = _rows_or_422(lambda: data.attrition(db, department_id, user.id, _limit(params.format)))
    slug, label = _scope_label(db, department_id)
    return render_report("attrition", "AI risk indicators (aggregate)", params.format, rows, [slug], [
        label, "Counts of stored model estimates and review alerts. Estimates are not observed departures or "
        "confirmed resignations; your own record is excluded."])


@router.get("/workforce")
def workforce_report(params: Annotated[DatedParams, Query()], db: DbSession, user: _reader("workforce")) -> Response:
    department_id = resolve_department(db, user, params.department_id)
    end = params.date_to or _today()
    if params.date_from is not None:
        start = params.date_from
    else:
        idx = wm.month_index(end.year, end.month) - 11
        start = date(idx // 12, idx % 12 + 1, 1)
    if start > end:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be on or before date_to")
    if wm.month_index(end.year, end.month) - wm.month_index(start.year, start.month) + 1 > MAX_TREND_MONTHS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The date range may cover at most {MAX_TREND_MONTHS} months")
    rows = data.workforce(_month_rows(db, department_id, start, end))
    slug, label = _scope_label(db, department_id)
    return render_report("workforce", "Workforce trend", params.format, rows, [slug, f"{start}_{end}"], [
        label, f"Months {start.isoformat()[:7]} to {end.isoformat()[:7]}. Headcount comes from captured snapshots; "
        "months without a snapshot are not observed."])


@router.get("/activity")
def activity_report(params: Annotated[ActivityReportParams, Query()], db: DbSession, user: _reader("activity")) -> Response:
    settings = get_settings()
    department_id = resolve_department(db, user, params.department_id)
    start_day, end_day = _days(params, 7, settings.activity_feed_max_window_days)
    tz = settings.org_tz
    start = datetime.combine(start_day, time.min, tzinfo=tz)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz)
    scope = feed.Scope(department_id, user.id, user.role in (UserRole.ADMIN, UserRole.HR) and department_id is None)
    rows = _rows_or_422(lambda: data.activity(db, scope, start, end, set(params.category or feed.SOURCES),
                                              set(params.kind or []) or None, _limit(params.format)))
    slug, label = _scope_label(db, department_id)
    return render_report("activity", "Workforce activity", params.format, rows, [slug, f"{start_day}_{end_day}"], [
        label, f"Recorded {start_day.isoformat()} to {end_day.isoformat()}. Only recorded events are shown."])
