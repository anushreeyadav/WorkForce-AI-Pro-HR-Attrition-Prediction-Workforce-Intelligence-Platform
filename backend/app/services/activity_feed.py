"""Read-only workforce activity feed and operational signals, assembled on demand from existing records.

Nothing is persisted or audited by reading. Each action comes from exactly ONE source (domain tables where they
exist; the audit log only for employee status changes, which have no domain history), so the same action is
never listed twice. Item identity: "<source>:<record id>:<action>".

Timestamps: `recorded_at` is when the platform stored the record (UTC). `source_event_at` is an independent
real-world time only where the source has one (e.g. attendance check-in time, date of joining); otherwise null.
Ordering: /feed uses recorded_at descending, then event_id descending (unchanged). Incremental updates use the
structured position key (recorded_at, source_rank, record id, action_rank), ascending.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Integer, Select, and_, cast, false, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Attendance,
    AttendanceStatus,
    AuditLog,
    Department,
    Employee,
    EmploymentStatus,
    Intervention,
    InterventionEvent,
    InterventionEventType,
    LeaveRequest,
    Recommendation,
    RecommendationScope,
    RiskAlert,
    RiskAlertEscalation,
    RiskEvent,
    WorkforceSnapshot,
    WorkforceSnapshotDepartment,
)
from app.models.risk_monitoring import ACTIVE_ALERT_STATUSES
from app.services import recommendation_rules
from app.services import workforce_analytics as wa
from app.services import workforce_math as wm

OBSERVED, RISK = "OBSERVED_EVENT", "RISK_EVENT"
AGGREGATE_SIGNAL, DATA_QUALITY_SIGNAL = "AGGREGATE_SIGNAL", "DATA_QUALITY_SIGNAL"

SOURCES = {
    "attendance": (OBSERVED, "attendance_records", "recorded_at = record creation; source_event_at = check-in time"),
    "leave": (OBSERVED, "leave_requests", "recorded_at = submission / review / cancellation time; no separate event time"),
    "employee_record": (OBSERVED, "employees", "recorded_at = record creation; source_event_at = date of joining"),
    "employee_status": (OBSERVED, "audit_logs (employee.status_changed)", "recorded_at = audit time only (may lag the real change)"),
    "risk_event": (RISK, "risk_events", "recorded_at = detection time of the monitoring run"),
    "risk_alert": (RISK, "risk_alerts", "recorded_at = creation / acknowledgement / resolution time"),
    "risk_escalation": (RISK, "risk_alert_escalations", "recorded_at = when an evaluation escalated the alert (not the deadline itself)"),
    "intervention": (OBSERVED, "intervention_events", "recorded_at = workflow event time"),
    "recommendation": (OBSERVED, "recommendations", "recorded_at = evaluation / review time"),
}
# Risk, intervention and recommendation items about the caller are never shown (existing policy).
SELF_EXCLUDED = {"risk_event", "risk_alert", "risk_escalation", "intervention", "recommendation"}
NOT_A_PREDICTION = ("Operational signals describe recorded data and its quality. They are not predictions of "
                    "resignation and say nothing about any individual's intentions.")


@dataclass(frozen=True)
class Scope:
    department_id: int | None  # None = organization (ADMIN/HR only)
    user_id: int
    include_organization_items: bool


@dataclass
class FeedItem:
    event_id: str
    kind: str
    category: str
    source_type: str
    source_id: int
    action: str
    description: str
    recorded_at: datetime
    source_event_at: datetime | date | None
    department_id: int | None
    employee_id: int | None
    priority: str | None = None

    def as_dict(self) -> dict:
        data = asdict(self)
        data["timestamp_basis"] = SOURCES[self.category][2]
        return data


def _employee_filter(stmt: Select, scope: Scope, category: str) -> Select:
    if scope.department_id is not None:
        stmt = stmt.where(Employee.department_id == scope.department_id)
    if category in SELF_EXCLUDED:
        stmt = stmt.where(or_(Employee.user_id.is_(None), Employee.user_id != scope.user_id))
    return stmt


def _window(column, start: datetime, end: datetime):
    return and_(column >= start, column < end)


# --- Sources: one bounded query per (source table, timestamp column) ---------------------------------
#
# Each unit yields one action. Its rows are ordered by (timestamp, id), which is also the order of the global
# position key (recorded_at, source_rank, record id, action_rank) restricted to that unit. That structured key
# is what incremental updates page by; ranks are fixed and must never be renumbered (cursors embed them).

Key = tuple[datetime, int, int, int]  # (recorded_at, source_rank, record id, action_rank)
BEFORE_ALL_RANK, AFTER_ALL_RANK = -1, 1000  # synthetic positions before / after every source at a timestamp


@dataclass(frozen=True)
class Unit:
    category: str
    source_rank: int
    action_rank: int
    timestamp: Any  # column holding recorded_at
    record_id: Any  # primary key of the source row
    priority: Any  # column used by the priority filter; None = this unit's items have no priority
    query: Callable[[Scope], Select]  # selects (recorded_at, record id, ...), joined and scoped
    item: Callable[[Any], "FeedItem | None"]


def _leave_query(column):
    def query(scope: Scope) -> Select:
        stmt = (select(column, LeaveRequest.id, LeaveRequest.status, Employee.id, Employee.department_id)
                .join(Employee, Employee.id == LeaveRequest.employee_id).where(column.is_not(None)))
        return _employee_filter(stmt, scope, "leave")
    return query


def _leave_item(action: str | None):
    def item(row) -> FeedItem:
        ts, rid, status, emp, dept = row
        # The review outcome is APPROVED or REJECTED; a later cancellation does not change the review event.
        name = action or ("rejected" if status.value == "REJECTED" else "approved")
        return FeedItem(f"leave_request:{rid}:{name}", OBSERVED, "leave", "leave_requests", rid, name,
                        f"Leave request {name}", ts, None, dept, emp)
    return item


def _alert_query(column):
    def query(scope: Scope) -> Select:
        stmt = (select(column, RiskAlert.id, RiskAlert.status, RiskAlert.priority, Employee.id, Employee.department_id)
                .join(Employee, Employee.id == RiskAlert.employee_id).where(column.is_not(None)))
        return _employee_filter(stmt, scope, "risk_alert")
    return query


def _alert_item(action: str | None):
    def item(row) -> FeedItem:
        ts, aid, status, priority, emp, dept = row
        name = action or status.value.lower()  # resolved or dismissed
        return FeedItem(f"risk_alert:{aid}:{name}", RISK, "risk_alert", "risk_alerts", aid, name, f"Risk alert {name}",
                        ts, None, dept, emp, priority.value)
    return item


def _attendance_query(scope: Scope) -> Select:
    stmt = (select(Attendance.created_at, Attendance.id, Attendance.check_in, Employee.id, Employee.department_id)
            .join(Employee, Employee.id == Attendance.employee_id).where(Attendance.check_in.is_not(None)))
    return _employee_filter(stmt, scope, "attendance")


def _attendance_item(row) -> FeedItem:
    created, rid, check_in, emp, dept = row
    return FeedItem(f"attendance:{rid}:checked_in", OBSERVED, "attendance", "attendance_records", rid, "checked_in",
                    "Attendance check-in recorded", created, check_in, dept, emp)


def _employee_query(scope: Scope) -> Select:
    stmt = select(Employee.created_at, Employee.id, Employee.date_of_joining, Employee.department_id)
    return _employee_filter(stmt, scope, "employee_record")


def _employee_item(row) -> FeedItem:
    created, eid, joined, dept = row
    return FeedItem(f"employee:{eid}:created", OBSERVED, "employee_record", "employees", eid, "created",
                    "Employee record created", created, joined, dept, eid)


def _status_query(scope: Scope) -> Select:
    # Scope is applied in SQL, so paging never loses rows to it. audit_logs.entity_id holds the employee id as text;
    # casting the AUDIT side keeps the employees primary key usable for the join (entity_type 'employee' rows only).
    stmt = (select(AuditLog.created_at, AuditLog.id, AuditLog.details, Employee.id, Employee.department_id)
            .join(Employee, Employee.id == cast(AuditLog.entity_id, Integer))
            .where(AuditLog.action == "employee.status_changed", AuditLog.entity_type == "employee"))
    return _employee_filter(stmt, scope, "employee_status")


def _status_item(row) -> FeedItem | None:
    created, aid, details, employee_id, dept = row
    change = ((details or {}).get("changes") or {}).get("status") or {}
    before, after = change.get("from"), change.get("to")
    if not before or not after:
        return None  # incomplete source: no description is invented
    return FeedItem(f"audit:{aid}:status_changed", OBSERVED, "employee_status", "audit_logs", aid, "status_changed",
                    f"Employment status changed from {before} to {after}", created, None, dept, employee_id)


def _risk_event_query(scope: Scope) -> Select:
    stmt = (select(RiskEvent.detected_at, RiskEvent.id, RiskEvent.event_type, Employee.id, Employee.department_id)
            .join(Employee, Employee.id == RiskEvent.employee_id))
    return _employee_filter(stmt, scope, "risk_event")


def _risk_event_item(row) -> FeedItem:
    detected, rid, event_type, emp, dept = row
    return FeedItem(f"risk_event:{rid}:detected", RISK, "risk_event", "risk_events", rid, event_type.value.lower(),
                    f"Risk monitoring event: {event_type.value.replace('_', ' ').lower()}", detected, None, dept, emp)


ESCALATION_LABELS = {"APPROACHING": "Risk alert review deadline approaching; reviewers notified",
                     "OVERDUE": "Risk alert review overdue; reviewers notified"}


def _escalation_query(scope: Scope) -> Select:
    stmt = (select(RiskAlertEscalation.created_at, RiskAlertEscalation.id, RiskAlertEscalation.stage,
                   RiskAlertEscalation.alert_priority, Employee.id, Employee.department_id)
            .join(RiskAlert, RiskAlert.id == RiskAlertEscalation.alert_id)
            .join(Employee, Employee.id == RiskAlert.employee_id))
    return _employee_filter(stmt, scope, "risk_escalation")


def _escalation_item(row) -> FeedItem:
    created, eid, stage, priority, emp, dept = row
    return FeedItem(f"risk_alert_escalation:{eid}:{stage.value.lower()}", RISK, "risk_escalation", "risk_alert_escalations",
                    eid, stage.value.lower(), ESCALATION_LABELS[stage.value], created, None, dept, emp, priority.value)


def _intervention_query(scope: Scope) -> Select:
    stmt = (select(InterventionEvent.created_at, InterventionEvent.id, InterventionEvent.event_type, Intervention.priority,
                   Employee.id, Employee.department_id)
            .join(Intervention, Intervention.id == InterventionEvent.intervention_id)
            .join(Employee, Employee.id == Intervention.employee_id))
    return _employee_filter(stmt, scope, "intervention")


def _intervention_item(row) -> FeedItem:
    created, eid, etype, priority, emp, dept = row
    label = ("Intervention follow-up note added" if etype == InterventionEventType.NOTE_ADDED  # note text never shown
             else f"Intervention {etype.value.lower().replace('_', ' ')}")
    return FeedItem(f"intervention_event:{eid}:{etype.value.lower()}", OBSERVED, "intervention", "intervention_events", eid,
                    etype.value.lower(), label, created, None, dept, emp, priority.value)


def _recommendation_query(column):
    def query(scope: Scope) -> Select:
        stmt = (select(column, Recommendation.id, Recommendation.status, Recommendation.scope, Recommendation.category,
                       Recommendation.priority, Recommendation.department_id, Employee.id, Employee.department_id)
                .outerjoin(Employee, Employee.id == Recommendation.employee_id)
                .where(column.is_not(None),
                       or_(Recommendation.employee_id.is_(None), Employee.user_id.is_(None), Employee.user_id != scope.user_id)))
        if scope.department_id is not None:
            stmt = stmt.where(or_(
                and_(Recommendation.scope == RecommendationScope.INDIVIDUAL, Employee.department_id == scope.department_id),
                and_(Recommendation.scope == RecommendationScope.DEPARTMENT, Recommendation.department_id == scope.department_id)))
        elif not scope.include_organization_items:
            stmt = stmt.where(false())
        return stmt
    return query


def _recommendation_item(generated: bool):
    def item(row) -> FeedItem:
        ts, rid, status, rscope, category, priority, rec_dept, emp, emp_dept = row
        dept = emp_dept if rscope == RecommendationScope.INDIVIDUAL else rec_dept
        if generated:
            label = category.value.replace("_", " ").lower()
            return FeedItem(f"recommendation:{rid}:generated", OBSERVED, "recommendation", "recommendations", rid,
                            "generated", f"Rule-based recommendation generated ({label})", ts, None, dept, emp, priority.value)
        action = status.value.lower()
        return FeedItem(f"recommendation:{rid}:{action}", OBSERVED, "recommendation", "recommendations", rid, action,
                        f"Recommendation {action}", ts, None, dept, emp, priority.value)
    return item


UNITS: tuple[Unit, ...] = (
    Unit("attendance", 0, 0, Attendance.created_at, Attendance.id, None, _attendance_query, _attendance_item),
    Unit("leave", 1, 0, LeaveRequest.created_at, LeaveRequest.id, None, _leave_query(LeaveRequest.created_at),
         _leave_item("submitted")),
    Unit("leave", 1, 1, LeaveRequest.reviewed_at, LeaveRequest.id, None, _leave_query(LeaveRequest.reviewed_at),
         _leave_item(None)),
    Unit("leave", 1, 2, LeaveRequest.cancelled_at, LeaveRequest.id, None, _leave_query(LeaveRequest.cancelled_at),
         _leave_item("cancelled")),
    Unit("employee_record", 2, 0, Employee.created_at, Employee.id, None, _employee_query, _employee_item),
    Unit("employee_status", 3, 0, AuditLog.created_at, AuditLog.id, None, _status_query, _status_item),
    Unit("risk_event", 4, 0, RiskEvent.detected_at, RiskEvent.id, None, _risk_event_query, _risk_event_item),
    Unit("risk_alert", 5, 0, RiskAlert.created_at, RiskAlert.id, RiskAlert.priority, _alert_query(RiskAlert.created_at),
         _alert_item("created")),
    Unit("risk_alert", 5, 1, RiskAlert.acknowledged_at, RiskAlert.id, RiskAlert.priority,
         _alert_query(RiskAlert.acknowledged_at), _alert_item("acknowledged")),
    Unit("risk_alert", 5, 2, RiskAlert.resolved_at, RiskAlert.id, RiskAlert.priority, _alert_query(RiskAlert.resolved_at),
         _alert_item(None)),
    Unit("risk_escalation", 6, 0, RiskAlertEscalation.created_at, RiskAlertEscalation.id, RiskAlertEscalation.alert_priority,
         _escalation_query, _escalation_item),
    Unit("intervention", 7, 0, InterventionEvent.created_at, InterventionEvent.id, Intervention.priority,
         _intervention_query, _intervention_item),
    Unit("recommendation", 8, 0, Recommendation.evaluated_at, Recommendation.id, Recommendation.priority,
         _recommendation_query(Recommendation.evaluated_at), _recommendation_item(True)),
    Unit("recommendation", 8, 1, Recommendation.reviewed_at, Recommendation.id, Recommendation.priority,
         _recommendation_query(Recommendation.reviewed_at), _recommendation_item(False)),
)


def selected_units(categories: set[str], kinds: set[str] | None, priorities: set[str] | None) -> list[Unit]:
    return [u for u in UNITS if u.category in categories and (not kinds or SOURCES[u.category][0] in kinds)
            and (not priorities or u.priority is not None)]


def _statement(unit: Unit, scope: Scope, priorities: set[str] | None) -> Select:
    stmt = unit.query(scope)
    if priorities:
        stmt = stmt.where(unit.priority.in_(sorted(priorities)))
    return stmt


def _after(unit: Unit, key: Key):
    """SQL condition: this unit's rows whose position key is strictly after `key`."""
    t, source_rank, record_id, action_rank = key
    if unit.source_rank > source_rank:
        return unit.timestamp >= t
    if unit.source_rank < source_rank:
        return unit.timestamp > t
    same_time = unit.record_id >= record_id if unit.action_rank > action_rank else unit.record_id > record_id
    # The leading `timestamp >= t` makes this a single index range scan on the timestamp index.
    return and_(unit.timestamp >= t, or_(unit.timestamp > t, same_time))


@dataclass
class FeedResult:
    items: list[FeedItem]
    truncated: bool
    sources_truncated: list[str]


def collect(db: Session, scope: Scope, start: datetime, end: datetime, categories: set[str], kinds: set[str] | None,
            priorities: set[str] | None, cap: int) -> FeedResult:
    """One query per selected unit (newest first, at most `cap` rows), merged and ordered by (recorded_at desc,
    event_id desc). A unit that hits the cap marks its category truncated."""
    items, truncated_sources = [], []
    for unit in selected_units(categories, kinds, priorities):
        rows = db.execute(_statement(unit, scope, priorities).where(_window(unit.timestamp, start, end))
                          .order_by(unit.timestamp.desc(), unit.record_id.desc()).limit(cap)).all()
        if len(rows) >= cap and unit.category not in truncated_sources:
            truncated_sources.append(unit.category)
        items.extend(i for i in map(unit.item, rows) if i is not None)
    seen, unique = set(), []
    for item in items:
        if item.event_id not in seen:  # defensive: identities are unique per unit by construction
            seen.add(item.event_id)
            unique.append(item)
    unique.sort(key=lambda i: (i.recorded_at, i.event_id), reverse=True)
    over = len(unique) > cap
    return FeedResult(unique[:cap], over or bool(truncated_sources), truncated_sources)


@dataclass
class UpdatesResult:
    items: list[tuple[Key, FeedItem]]
    has_more: bool
    next_key: Key


def updates(db: Session, scope: Scope, after: Key, lower: datetime, upper: datetime, categories: set[str],
            kinds: set[str] | None, priorities: set[str] | None, limit: int) -> UpdatesResult:
    """Items with position key > `after` and lower <= recorded_at <= upper, oldest first, at most `limit`.

    Each unit returns at most limit+1 rows in its own (timestamp, id) order, so a fixed number of bounded queries
    determines the next page exactly (k-way merge). If a unit was cut off, only items at or before its last
    fetched row are certain; that position is the "frontier" and nothing after it is returned in this page.
    """
    candidates: list[tuple[Key, FeedItem]] = []
    frontier: Key | None = None
    for unit in selected_units(categories, kinds, priorities):
        rows = db.execute(_statement(unit, scope, priorities)
                          .where(unit.timestamp >= lower, unit.timestamp <= upper, _after(unit, after))
                          .order_by(unit.timestamp.asc(), unit.record_id.asc()).limit(limit + 1)).all()
        for row in rows:
            item = unit.item(row)
            if item is not None:
                candidates.append(((row[0], unit.source_rank, row[1], unit.action_rank), item))
        if len(rows) > limit:
            last = (rows[-1][0], unit.source_rank, rows[-1][1], unit.action_rank)
            frontier = last if frontier is None else min(frontier, last)
    candidates.sort(key=lambda c: c[0])
    if frontier is not None:
        candidates = [c for c in candidates if c[0] <= frontier]
    page = candidates[:limit]
    if len(candidates) > limit:
        return UpdatesResult(page, True, page[-1][0])
    if frontier is not None:
        return UpdatesResult(page, True, frontier)
    return UpdatesResult(page, False, max(after, (upper, AFTER_ALL_RANK, 0, 0)))


def latest_recorded(db: Session, scope: Scope, start: datetime, end: datetime) -> datetime | None:
    """Newest recorded_at in [start, end) across all units in scope (one bounded query per unit)."""
    latest = None
    for unit in UNITS:
        value = db.scalar(unit.query(scope).where(_window(unit.timestamp, start, end))
                          .with_only_columns(func.max(unit.timestamp)))
        if value is not None and (latest is None or value > latest):
            latest = value
    return latest


def category_counts(db: Session, scope: Scope, start: datetime, end: datetime) -> dict[str, int]:
    """Largest per-unit row count in the window for each category (one COUNT per unit), for comparison with
    the per-unit feed cap."""
    counts: dict[str, int] = {}
    for unit in UNITS:
        n = db.scalar(unit.query(scope).where(_window(unit.timestamp, start, end))
                      .with_only_columns(func.count(unit.record_id))) or 0
        counts[unit.category] = max(counts.get(unit.category, 0), n)
    return counts


# --- Department activity -------------------------------------------------------------------------


def department_activity(db: Session, settings: Settings, department_id: int, user_id: int, start: datetime, end: datetime) -> dict:
    tz = settings.org_tz
    scope = Scope(department_id, user_id, False)

    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    active_employees = count(select(func.count(Employee.id)).where(Employee.department_id == department_id,
                                                                   Employee.status != EmploymentStatus.INACTIVE))
    att_rows = dict(db.execute(
        select(Attendance.status, func.count(Attendance.id)).join(Employee, Employee.id == Attendance.employee_id)
        .where(Employee.department_id == department_id, _window(Attendance.created_at, start, end)).group_by(Attendance.status)).all())
    att_total = sum(att_rows.values())
    attendance = ({"status": "available", "records": att_total,
                   "records_by_status": {s.value: att_rows.get(s, 0) for s in AttendanceStatus}}
                  if att_total else
                  {"status": "unavailable", "records": None, "records_by_status": None,
                   "reason": "No attendance records in this window; activity is unknown, not zero"})

    leave_base = select(func.count(LeaveRequest.id)).join(Employee, Employee.id == LeaveRequest.employee_id).where(
        Employee.department_id == department_id)
    submitted = count(leave_base.where(_window(LeaveRequest.created_at, start, end)))
    reviewed_rows = dict(db.execute(
        select(LeaveRequest.status, func.count(LeaveRequest.id)).join(Employee, Employee.id == LeaveRequest.employee_id)
        .where(Employee.department_id == department_id, _window(LeaveRequest.reviewed_at, start, end))
        .group_by(LeaveRequest.status)).all())
    approved = sum(n for s, n in reviewed_rows.items() if s.value != "REJECTED")
    rejected = sum(n for s, n in reviewed_rows.items() if s.value == "REJECTED")
    cancelled = count(leave_base.where(_window(LeaveRequest.cancelled_at, start, end)))

    transitions = wa.status_transitions(db, start.astimezone(tz).date(), (end - timedelta(microseconds=1)).astimezone(tz).date(),
                                        tz, department_id)
    alert_stmt = _employee_filter(select(RiskAlert.status, func.count(RiskAlert.id)).join(Employee, Employee.id == RiskAlert.employee_id)
                                  .where(_window(RiskAlert.created_at, start, end)), scope, "risk_alert").group_by(RiskAlert.status)
    alerts_by_status = {s.value: n for s, n in db.execute(alert_stmt)}
    intervention_stmt = _employee_filter(
        select(InterventionEvent.event_type, func.count(InterventionEvent.id))
        .join(Intervention, Intervention.id == InterventionEvent.intervention_id).join(Employee, Employee.id == Intervention.employee_id)
        .where(_window(InterventionEvent.created_at, start, end)), scope, "intervention").group_by(InterventionEvent.event_type)
    intervention_events = {t.value: n for t, n in db.execute(intervention_stmt)}
    obs = wa.observations(db, department_id)
    return {
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "active_employees_now": active_employees,
        "attendance": attendance,
        "leave": {"submitted": submitted, "approved": approved, "rejected": rejected, "cancelled": cancelled,
                  "approval_rate": round(approved / (approved + rejected), 4) if approved + rejected else None,
                  "approval_rate_definition": "approved / (approved + rejected) among requests reviewed in the window"},
        "employee_status": {"recorded_deactivations": sum(t.kind == "departure" for t in transitions),
                            "recorded_reactivations": sum(t.kind == "reactivation" for t in transitions),
                            "note": "Dated by when the change was recorded, which may lag the real change"},
        "risk_alerts_created": {"total": sum(alerts_by_status.values()), "current_status": alerts_by_status,
                                "note": "Alerts are review aids based on estimates; they are not departures"},
        "intervention_events": intervention_events,
        "snapshots": {"department_snapshot_count": len(obs),
                      "latest_snapshot_date": obs[-1].snapshot_date.isoformat() if obs else None},
        "data_quality": {"inactive_without_recorded_departure": wa.inactive_without_recorded_departure(db, department_id)},
        "note": ("Operational activity counts. They are not attrition outcomes and do not indicate any individual's "
                 "engagement or intentions."),
    }


# --- Signals ---------------------------------------------------------------------------------------


def _signal(signal_id, kind, status, scope_level, department_id, explanation, evidence, source, coverage, now) -> dict:
    return {"signal_id": signal_id, "kind": kind, "status": status, "scope": scope_level, "department_id": department_id,
            "explanation": explanation, "evidence": evidence, "source": source, "coverage": coverage,
            "evaluated_at": now.isoformat()}


def _department_observations(db: Session, departments: list[int]) -> dict[int, list[wa.Observation]]:
    """Department snapshot observations for many departments in ONE query (same rows as wa.observations)."""
    found: dict[int, list[wa.Observation]] = {}
    rows = db.execute(
        select(WorkforceSnapshotDepartment.department_id, WorkforceSnapshot.snapshot_date,
               WorkforceSnapshotDepartment.total_employees, WorkforceSnapshotDepartment.active_employees,
               WorkforceSnapshotDepartment.on_leave_employees, WorkforceSnapshotDepartment.inactive_employees)
        .join(WorkforceSnapshot, WorkforceSnapshot.id == WorkforceSnapshotDepartment.snapshot_id)
        .where(WorkforceSnapshotDepartment.department_id.in_(departments)).order_by(WorkforceSnapshot.snapshot_date))
    for dept, *values in rows:
        found.setdefault(dept, []).append(wa.Observation(*values))
    return found


def signals(db: Session, settings: Settings, scope: Scope, now: datetime) -> list[dict]:
    tz = settings.org_tz
    today = now.astimezone(tz).date()
    results = []
    if scope.department_id is None:
        latest = db.scalar(select(func.max(WorkforceSnapshot.snapshot_date)))
        age = (today - latest).days if latest else None
        stale = age is None or age > settings.workforce_snapshot_max_age_days
        results.append(_signal(
            "SNAPSHOT_STALE", DATA_QUALITY_SIGNAL, "ACTIVE" if stale else "CLEAR", "organization", None,
            "No workforce snapshot has been captured" if latest is None else
            f"The latest workforce snapshot is {age} days old (limit {settings.workforce_snapshot_max_age_days})",
            {"latest_snapshot_date": latest.isoformat() if latest else None, "age_days": age}, "workforce_snapshots",
            None, now))
        unknown = wa.inactive_without_recorded_departure(db, None)
        results.append(_signal("UNKNOWN_DEPARTURE_DATES", DATA_QUALITY_SIGNAL, "ACTIVE" if unknown else "CLEAR",
                               "organization", None, f"{unknown} inactive employee(s) have no recorded departure date",
                               {"count": unknown}, "employees + audit_logs", None, now))

    departments = [scope.department_id] if scope.department_id is not None else list(db.scalars(select(Department.id).order_by(Department.id)))
    gap_start = datetime.combine(today - timedelta(days=settings.activity_attendance_gap_days - 1), time.min, tzinfo=tz)
    review_cutoff = now - timedelta(days=settings.activity_alert_review_days)

    active_counts = dict(db.execute(select(Employee.department_id, func.count(Employee.id))
                                    .where(Employee.status != EmploymentStatus.INACTIVE, Employee.department_id.in_(departments))
                                    .group_by(Employee.department_id)).all())
    attendance_counts = dict(db.execute(select(Employee.department_id, func.count(Attendance.id))
                                        .join(Employee, Employee.id == Attendance.employee_id)
                                        .where(Attendance.created_at >= gap_start, Employee.department_id.in_(departments))
                                        .group_by(Employee.department_id)).all())
    overdue_stmt = _employee_filter(
        select(Employee.department_id, func.count(RiskAlert.id)).join(Employee, Employee.id == RiskAlert.employee_id)
        .where(RiskAlert.status.in_(ACTIVE_ALERT_STATUSES), RiskAlert.first_detected_at < review_cutoff,
               Employee.department_id.in_(departments)), Scope(None, scope.user_id, True), "risk_alert").group_by(Employee.department_id)
    overdue_counts = dict(db.execute(overdue_stmt).all())
    snapshots_by_department = _department_observations(db, departments)

    for dept in departments:
        active = active_counts.get(dept, 0)
        records = attendance_counts.get(dept, 0)
        if active == 0:
            results.append(_signal("ATTENDANCE_DATA_GAP", DATA_QUALITY_SIGNAL, "INSUFFICIENT_DATA", "department", dept,
                                   "No current employees in this department", {"active_employees": 0}, "attendance_records",
                                   None, now))
        else:
            results.append(_signal(
                "ATTENDANCE_DATA_GAP", DATA_QUALITY_SIGNAL, "ACTIVE" if records == 0 else "CLEAR", "department", dept,
                f"{'No' if records == 0 else records} attendance record(s) in the last {settings.activity_attendance_gap_days} "
                f"days for {active} current employee(s)" + ("; attendance data may be incomplete" if records == 0 else ""),
                {"active_employees": active, "attendance_records": records, "window_days": settings.activity_attendance_gap_days},
                "attendance_records", None, now))

        month_points = sorted((idx, o.active) for idx, o in wa.month_end(snapshots_by_department.get(dept, [])).items())
        coverage = {"observed_months": len(month_points)}
        if len(month_points) < 4:
            results.append(_signal("HEADCOUNT_DECLINE", AGGREGATE_SIGNAL, "INSUFFICIENT_DATA", "department", dept,
                                   "Fewer than four observed month-end snapshots", {}, "workforce_snapshots", coverage, now))
        else:
            declining = recommendation_rules.consecutive_declines(month_points)
            values = [v for _, v in month_points[-4:]]
            results.append(_signal(
                "HEADCOUNT_DECLINE", AGGREGATE_SIGNAL, "ACTIVE" if declining else "CLEAR", "department", dept,
                ("Month-end active headcount declined three months in a row" if declining
                 else "No three-month consecutive decline in the latest observed month-ends"),
                {"last_four_month_ends": values, "months": [wm.index_to_month(i) for i, _ in month_points[-4:]]},
                "workforce_snapshots", coverage, now))

        overdue = overdue_counts.get(dept, 0)
        results.append(_signal(
            "ALERTS_AWAITING_REVIEW", AGGREGATE_SIGNAL, "ACTIVE" if overdue else "CLEAR", "department", dept,
            f"{overdue} active risk alert(s) first detected more than {settings.activity_alert_review_days} days ago",
            {"count": overdue, "review_days": settings.activity_alert_review_days}, "risk_alerts", None, now))
    return results


# --- Monitoring status ---------------------------------------------------------------------------------


def active_alert_rows(db: Session, scope: Scope) -> list[tuple]:
    """(priority, status, first_detected_at, acknowledged_at, resolved_at) of active alerts in scope (self excluded).
    Bounded by the one-active-alert-per-employee rule."""
    stmt = (select(RiskAlert.priority, RiskAlert.status, RiskAlert.first_detected_at, RiskAlert.acknowledged_at,
                   RiskAlert.resolved_at)
            .join(Employee, Employee.id == RiskAlert.employee_id).where(RiskAlert.status.in_(ACTIVE_ALERT_STATUSES)))
    return db.execute(_employee_filter(stmt, scope, "risk_alert")).all()


def latest_snapshot(db: Session, department_id: int | None) -> tuple[date, datetime] | None:
    """(snapshot_date, captured_at) of the newest snapshot (containing the department, if given)."""
    stmt = select(WorkforceSnapshot.snapshot_date, WorkforceSnapshot.captured_at)
    if department_id is not None:
        stmt = stmt.join(WorkforceSnapshotDepartment, WorkforceSnapshotDepartment.snapshot_id == WorkforceSnapshot.id).where(
            WorkforceSnapshotDepartment.department_id == department_id)
    row = db.execute(stmt.order_by(WorkforceSnapshot.snapshot_date.desc(), WorkforceSnapshot.id.desc()).limit(1)).first()
    return tuple(row) if row else None
