"""Workforce analytics data access. Read-only except snapshot capture. Aggregates only.

Sources and their reliability:
- Current headcount: live employee records (status ACTIVE / ON_LEAVE / INACTIVE).
- Joiners: employees.date_of_joining (HR-entered effective date). Employees are never deleted, so leavers
  are still counted as joiners in their joining month.
- Recorded departures: audit rows "employee.status_changed" whose status changed to INACTIVE from another
  status, dated by when the change was RECORDED (organization time zone). This can lag the real last
  working day. Employees created as INACTIVE, or who left before audit records existed, have no departure
  date and are excluded (reported as a data-quality warning).
- Historical headcount: captured snapshots only. It is never reconstructed from current records.
Department attribution for departures uses the employee's current department (moves are not tracked).
Snapshots are captured manually (POST /snapshots/capture, ADMIN/HR); there is no scheduler. Reads never capture.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    AlertStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Department,
    Employee,
    EmploymentStatus,
    RiskAlert,
    RiskCategory,
    User,
    WorkforceSnapshot,
    WorkforceSnapshotDepartment,
)
from app.services import workforce_math as wm
from ml.attrition.features import FEATURE_SCHEMA_VERSION

DEPARTURE_DEFINITION = ("Status changes to INACTIVE recorded through the employee status endpoint, dated by the "
                        "recording time in the organization time zone")
JOINER_DEFINITION = "Employees whose date_of_joining falls in the period (current and former employees)"
AUDIT_LOOKUP_CHUNK = 500  # bounded IN lists when reading the status history of specific employees
STATUS_FIELDS = {EmploymentStatus.ACTIVE: "active_employees", EmploymentStatus.ON_LEAVE: "on_leave_employees",
                 EmploymentStatus.INACTIVE: "inactive_employees"}


class SnapshotExists(Exception):
    def __init__(self, snapshot_id: int) -> None:
        super().__init__(snapshot_id)
        self.snapshot_id = snapshot_id


def _empty_counts() -> dict[str, int]:
    return {"total_employees": 0, "active_employees": 0, "on_leave_employees": 0, "inactive_employees": 0}


def current_counts(db: Session, department_id: int | None = None) -> tuple[dict[str, int], dict[int, dict[str, int]]]:
    """(overall counts, per-department counts) from live records. Every department appears, even with 0."""
    departments = select(Department.id)
    if department_id is not None:
        departments = departments.where(Department.id == department_id)
    per_department = {d: _empty_counts() for d in db.scalars(departments)}
    stmt = select(Employee.department_id, Employee.status, func.count(Employee.id)).group_by(Employee.department_id, Employee.status)
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    overall = _empty_counts()
    for dept, employment_status, n in db.execute(stmt):
        counts = per_department.setdefault(dept, _empty_counts())
        for bucket in (counts, overall):
            bucket[STATUS_FIELDS[employment_status]] += n
            bucket["total_employees"] += n
    return overall, per_department


def joiners(db: Session, start: date, end: date, department_id: int | None = None) -> list[tuple[date, int]]:
    """(date_of_joining, department_id) for employees who joined in [start, end]."""
    stmt = select(Employee.date_of_joining, Employee.department_id).where(
        Employee.date_of_joining >= start, Employee.date_of_joining <= end)
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    return [(d, dept) for d, dept in db.execute(stmt)]


def _local_bounds(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (datetime.combine(start, time.min, tzinfo=tz), datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz))


@dataclass(frozen=True)
class StatusTransition:
    recorded_on: date
    employee_id: int
    department_id: int
    kind: str  # "departure" | "reactivation"


def status_transitions(db: Session, start: date, end: date, tz: ZoneInfo, department_id: int | None = None) -> list[StatusTransition]:
    """Departures (→ INACTIVE) and reactivations (INACTIVE →) recorded in [start, end] (org-local dates)."""
    lower, upper = _local_bounds(start, end, tz)
    rows = db.execute(
        select(AuditLog.created_at, AuditLog.entity_id, AuditLog.details)
        .where(AuditLog.action == "employee.status_changed", AuditLog.created_at >= lower, AuditLog.created_at < upper)
        .order_by(AuditLog.created_at, AuditLog.id)
    ).all()
    ids = {int(entity_id) for _, entity_id, _ in rows if entity_id and str(entity_id).isdigit()}
    departments = dict(db.execute(select(Employee.id, Employee.department_id).where(Employee.id.in_(ids))).all()) if ids else {}

    found = []
    for created_at, entity_id, details in rows:
        change = ((details or {}).get("changes") or {}).get("status") or {}
        before, after = change.get("from"), change.get("to")
        employee_id = int(entity_id) if entity_id and str(entity_id).isdigit() else None
        if employee_id not in departments or before == after:
            continue
        kind = ("departure" if after == EmploymentStatus.INACTIVE.value
                else "reactivation" if before == EmploymentStatus.INACTIVE.value else None)
        if kind is None:
            continue
        dept = departments[employee_id]
        if department_id is not None and dept != department_id:
            continue
        found.append(StatusTransition(created_at.astimezone(tz).date(), employee_id, dept, kind))
    return found


def inactive_without_recorded_departure(db: Session, department_id: int | None = None) -> int:
    """INACTIVE employees with no recorded transition to INACTIVE: their departure dates are unknown."""
    stmt = select(Employee.id).where(Employee.status == EmploymentStatus.INACTIVE)
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    inactive = sorted(db.scalars(stmt))
    if not inactive:
        return 0
    recorded = set()
    # Only the status history of these employees is read (ix_audit_logs_entity), in bounded chunks, instead of the
    # whole status-change history of the organization.
    for start in range(0, len(inactive), AUDIT_LOOKUP_CHUNK):
        chunk = [str(i) for i in inactive[start:start + AUDIT_LOOKUP_CHUNK]]
        for entity_id, details in db.execute(select(AuditLog.entity_id, AuditLog.details).where(
                AuditLog.entity_type == "employee", AuditLog.entity_id.in_(chunk),
                AuditLog.action == "employee.status_changed")):
            change = ((details or {}).get("changes") or {}).get("status") or {}
            if change.get("to") == EmploymentStatus.INACTIVE.value and entity_id and str(entity_id).isdigit():
                recorded.add(int(entity_id))
    return len(set(inactive) - recorded)


# --- Snapshots ---------------------------------------------------------------------------------


def capture_snapshot(db: Session, actor: User, now: datetime, tz: ZoneInfo) -> WorkforceSnapshot:
    today = now.astimezone(tz).date()
    existing = db.scalar(select(WorkforceSnapshot.id).where(WorkforceSnapshot.snapshot_date == today))
    if existing is not None:
        raise SnapshotExists(existing)
    period_start = today.replace(day=1)
    overall, per_department = current_counts(db)
    names = dict(db.execute(select(Department.id, Department.name)).all())
    joined = joiners(db, period_start, today)
    departed = [t for t in status_transitions(db, period_start, today, tz) if t.kind == "departure"]
    unknown = inactive_without_recorded_departure(db)

    warnings = []
    if unknown:
        warnings.append(f"{unknown} inactive employee(s) have no recorded departure date and are excluded from departures")
    snapshot = WorkforceSnapshot(
        snapshot_date=today, captured_at=now, captured_by_user_id=actor.id, period_start=period_start, **overall,
        joiners_in_period=len(joined), recorded_departures_in_period=len(departed), source="live_employee_records",
        data_quality={"departure_definition": DEPARTURE_DEFINITION, "joiner_definition": JOINER_DEFINITION,
                      "inactive_without_recorded_departure": unknown, "warnings": warnings})
    db.add(snapshot)
    db.flush()
    for dept, counts in sorted(per_department.items()):
        db.add(WorkforceSnapshotDepartment(
            snapshot_id=snapshot.id, department_id=dept, department_name=names.get(dept, f"Department {dept}"), **counts,
            joiners_in_period=sum(1 for _, d in joined if d == dept),
            recorded_departures_in_period=sum(1 for t in departed if t.department_id == dept)))
    db.flush()
    return snapshot


@dataclass(frozen=True)
class Observation:
    snapshot_date: date
    total: int
    active: int
    on_leave: int
    inactive: int


def observations(db: Session, department_id: int | None, start: date | None = None, end: date | None = None) -> list[Observation]:
    """All snapshot observations for the scope, sorted by date. Department scope uses department rows only."""
    if department_id is None:
        stmt = select(WorkforceSnapshot.snapshot_date, WorkforceSnapshot.total_employees, WorkforceSnapshot.active_employees,
                      WorkforceSnapshot.on_leave_employees, WorkforceSnapshot.inactive_employees)
        date_col = WorkforceSnapshot.snapshot_date
    else:
        stmt = (select(WorkforceSnapshot.snapshot_date, WorkforceSnapshotDepartment.total_employees,
                       WorkforceSnapshotDepartment.active_employees, WorkforceSnapshotDepartment.on_leave_employees,
                       WorkforceSnapshotDepartment.inactive_employees)
                .join(WorkforceSnapshotDepartment, WorkforceSnapshotDepartment.snapshot_id == WorkforceSnapshot.id)
                .where(WorkforceSnapshotDepartment.department_id == department_id))
        date_col = WorkforceSnapshot.snapshot_date
    if start is not None:
        stmt = stmt.where(date_col >= start)
    if end is not None:
        stmt = stmt.where(date_col <= end)
    return [Observation(*row) for row in db.execute(stmt.order_by(date_col))]


def month_end(obs: list[Observation]) -> dict[int, Observation]:
    """The last observation in each calendar month represents that month."""
    result: dict[int, Observation] = {}
    for item in obs:
        result[wm.month_index(item.snapshot_date.year, item.snapshot_date.month)] = item
    return result


# --- Stored AI risk indicators (separate from observed outcomes) -------------------------------------


def risk_indicators(db: Session, department_id: int | None, exclude_user_id: int) -> dict:
    """Latest schema-compatible stored prediction per current employee in scope, counted by category.
    Never calls the model; never lists individuals; the caller's own record is excluded."""
    employees = select(Employee.id).where(Employee.status != EmploymentStatus.INACTIVE,
                                          (Employee.user_id.is_(None)) | (Employee.user_id != exclude_user_id))
    if department_id is not None:
        employees = employees.where(Employee.department_id == department_id)
    employee_ids = set(db.scalars(employees))

    latest: dict[int, tuple] = {}
    if employee_ids:
        for pred_id, emp, predicted_at, category, schema, model_schema in db.execute(
            select(AttritionPrediction.id, AttritionPrediction.employee_id, AttritionPrediction.predicted_at,
                   AttritionPrediction.risk_category, AttritionPrediction.feature_schema_version,
                   AttritionModelVersion.feature_schema_version)
            .join(AttritionModelVersion, AttritionModelVersion.id == AttritionPrediction.model_version_id)
            .where(AttritionPrediction.employee_id.in_(employee_ids))
        ):
            key = (predicted_at, pred_id)
            if emp not in latest or key > latest[emp][0]:
                latest[emp] = (key, category, schema == FEATURE_SCHEMA_VERSION and model_schema == FEATURE_SCHEMA_VERSION)
    by_category = {c.value: 0 for c in RiskCategory}
    incompatible = 0
    for _, category, compatible in latest.values():
        if compatible:
            by_category[category.value] += 1
        else:
            incompatible += 1

    alerts = {s.value: 0 for s in AlertStatus}
    if employee_ids:
        for alert_status, n in db.execute(select(RiskAlert.status, func.count(RiskAlert.id))
                                          .where(RiskAlert.employee_id.in_(employee_ids)).group_by(RiskAlert.status)):
            alerts[alert_status.value] = n
    return {
        "current_employees_in_scope": len(employee_ids),
        "employees_with_compatible_prediction": sum(by_category.values()),
        "employees_without_prediction": len(employee_ids) - len(latest),
        "employees_with_incompatible_prediction_only": incompatible,
        "latest_prediction_by_category": by_category,
        "risk_alerts_by_status": alerts,
        "note": ("Stored model estimates and review alerts. They are not observed departures and not confirmed "
                 "resignations."),
    }


# --- Stability indicator (shared by the analytics API, reports and recommendations) -------------------------

STABILITY_FORMULA = ("score = 0.5*turnover + 0.25*net_change + 0.25*volatility; turnover = 100*max(0, 1 - "
                     "annualized_turnover/0.30); net_change = 100*max(0, 1 - |net_change_rate|/0.20); volatility = "
                     "100*max(0, 1 - coefficient_of_variation/0.10). Range 0-100, higher is more stable.")
STABILITY_NOTE = ("Aggregate heuristic describing the workforce as a whole, not a validated prediction. It says nothing "
                  "about any individual and must not drive employment decisions.")


def stability_window_start(settings: Settings, today: date) -> date:
    """First day of the stability window: the last `workforce_stability_window_months` calendar months."""
    idx = wm.month_index(today.year, today.month) - (settings.workforce_stability_window_months - 1)
    return date(idx // 12, idx % 12 + 1, 1)


def _stability_result(settings: Settings, today: date, window_start: date, obs: list[tuple[date, int]],
                      departure_dates: list[date]) -> dict:
    """obs: (snapshot date, active) in the window, sorted. departure_dates: recorded departures in the window."""
    departures = None
    if len(obs) >= 2:
        # Departures recorded after the first observation, up to and including the last one.
        departures = sum(1 for d in departure_dates if obs[0][0] < d <= obs[-1][0])
    result = wm.stability(wm.StabilityInputs(
        observations=obs, recorded_departures=departures,
        latest_age_days=(today - obs[-1][0]).days if obs else None,
        max_age_days=settings.workforce_snapshot_max_age_days,
        min_average_headcount=settings.workforce_stability_min_average_headcount))
    result["window"] = {"from": window_start.isoformat(), "to": today.isoformat(),
                        "months": settings.workforce_stability_window_months}
    result["latest_snapshot_date"] = obs[-1][0].isoformat() if obs else None
    result["recorded_departures_in_window"] = departures
    result["minimum_average_headcount"] = settings.workforce_stability_min_average_headcount
    result["formula"] = STABILITY_FORMULA
    result["note"] = STABILITY_NOTE
    return result


def stability_report(db: Session, settings: Settings, department_id: int | None, today: date,
                     window_observations: list[Observation] | None = None) -> dict:
    """The stability indicator for the organization (None) or one department, as of `today` (org-local)."""
    window_start = stability_window_start(settings, today)
    obs = (window_observations if window_observations is not None
           else observations(db, department_id, window_start, today))
    departure_dates = []
    if len(obs) >= 2:
        departure_dates = [t.recorded_on for t in status_transitions(
            db, obs[0].snapshot_date + timedelta(days=1), obs[-1].snapshot_date, settings.org_tz, department_id)
            if t.kind == "departure"]
    return _stability_result(settings, today, window_start, [(o.snapshot_date, o.active) for o in obs], departure_dates)


def stability_by_department(db: Session, settings: Settings, today: date, department_ids: list[int]) -> dict[int, dict]:
    """stability_report for several departments with two grouped queries instead of two per department.
    Gives exactly the same result as calling stability_report for each department."""
    window_start = stability_window_start(settings, today)
    obs: dict[int, list[tuple[date, int]]] = {d: [] for d in department_ids}
    if department_ids:
        rows = db.execute(
            select(WorkforceSnapshotDepartment.department_id, WorkforceSnapshot.snapshot_date,
                   WorkforceSnapshotDepartment.active_employees)
            .join(WorkforceSnapshot, WorkforceSnapshot.id == WorkforceSnapshotDepartment.snapshot_id)
            .where(WorkforceSnapshotDepartment.department_id.in_(department_ids),
                   WorkforceSnapshot.snapshot_date >= window_start, WorkforceSnapshot.snapshot_date <= today)
            .order_by(WorkforceSnapshot.snapshot_date))
        for department_id, day, active in rows:
            obs[department_id].append((day, active))
    departures: dict[int, list[date]] = {d: [] for d in department_ids}
    if any(len(v) >= 2 for v in obs.values()):
        for t in status_transitions(db, window_start, today, settings.org_tz):
            if t.kind == "departure" and t.department_id in departures:
                departures[t.department_id].append(t.recorded_on)
    return {d: _stability_result(settings, today, window_start, obs[d], departures[d]) for d in department_ids}
