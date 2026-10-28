"""Workforce activity feed and operational signals over SYNTHETIC records. Reading must never write anything."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import (
    AlertPriority,
    AlertStatus,
    Attendance,
    AttendanceStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Employee,
    Intervention,
    InterventionCategory,
    InterventionEvent,
    InterventionEventType,
    InterventionPriority,
    InterventionStatus,
    LeaveRequest,
    LeaveRequestStatus,
    ModelVersionStatus,
    Notification,
    Recommendation,
    RiskAlert,
    RiskCategory,
    RiskEvent,
    RiskEventType,
    UserRole,
    WorkforceSnapshot,
    WorkforceSnapshotDepartment,
)
from app.models.base import utcnow
from app.services import activity_feed as feed
from ml.attrition.features import FEATURE_SCHEMA_VERSION

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
BASE = "/api/workforce-activity"
SECRET_REASON = "ZZ-private-medical-reason-ZZ"
SECRET_NOTE = "ZZ-confidential-followup-note-ZZ"
SECRET_REVIEW = "ZZ-reviewer-notes-ZZ"
SENTINEL_SCORE = "81.37"
SENTINEL_FEATURES = {"tenure_months": 987, "avg_overtime_minutes_90d": 654.5}


def today() -> date:
    return utcnow().astimezone(get_settings().org_tz).date()


def ago(hours: float) -> datetime:
    return utcnow() - timedelta(hours=hours)


@pytest.fixture
def org(staff, headers_for):
    manager, manager_h = staff(MANAGER, department="Engineering", full_name="Mona Manager")
    eng, eng_h = staff(department="Engineering", full_name="Edith Engineer")
    eng2, _ = staff(department="Engineering", full_name="Ezra Engineer")
    sales, _ = staff(department="Sales", full_name="Sam Seller")
    return {"manager": manager, "manager_h": manager_h, "eng": eng, "eng_h": eng_h, "eng2": eng2, "sales": sales,
            "hr": headers_for(HR, "hr.activity@example.com"), "admin": headers_for(ADMIN, "admin.activity@example.com")}


@pytest.fixture
def add(db, make_leave_type, make_policy):
    """Factories that insert synthetic source records directly, with controlled timestamps."""
    state: dict = {"work_day": date(2020, 1, 1)}

    def attendance(employee, created_at=None, check_in=None, status=AttendanceStatus.PRESENT):
        created_at = created_at or ago(1)
        state["work_day"] += timedelta(days=1)  # work_date is unique per employee and not used by the feed
        record = Attendance(employee_id=employee.id, work_date=state["work_day"],
                            check_in=check_in or created_at, status=status)
        record.created_at = created_at
        db.add(record)
        db.commit()
        return record

    def leave(employee, created_at=None, status=LeaveRequestStatus.PENDING, reviewed_at=None, cancelled_at=None):
        if "policy" not in state:
            state["policy"] = make_policy(make_leave_type())
        policy = state["policy"]
        request = LeaveRequest(employee_id=employee.id, leave_type_id=policy.leave_type_id, policy_id=policy.id,
                               start_date=today() + timedelta(days=10), end_date=today() + timedelta(days=11),
                               working_days=2, reason=SECRET_REASON, status=status, reviewed_at=reviewed_at,
                               review_comment=SECRET_REVIEW if reviewed_at else None, cancelled_at=cancelled_at)
        request.created_at = created_at or ago(5)
        db.add(request)
        db.commit()
        return request

    def alert(employee, created_at=None, status=AlertStatus.OPEN, priority=AlertPriority.HIGH, first_detected_at=None,
              acknowledged_at=None, resolved_at=None):
        if "model" not in state:
            state["model"] = AttritionModelVersion(
                model_version="lr-20260101000000-activity", feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=ModelVersionStatus.ACTIVE, trained_at=utcnow(), dataset_fingerprint="0" * 64,
                artifact_sha256="0" * 64, training_rows=100, evaluation={})
            db.add(state["model"])
            db.flush()
        model = state["model"]
        prediction = AttritionPrediction(
            employee_id=employee.id, model_version_id=model.id, feature_schema_version=FEATURE_SCHEMA_VERSION,
            risk_score=Decimal(SENTINEL_SCORE), risk_category=RiskCategory.HIGH, features=SENTINEL_FEATURES,
            explanation={"factors": [], "missing_inputs": []}, predicted_at=utcnow())
        db.add(prediction)
        db.flush()
        created_at = created_at or ago(2)
        record = RiskAlert(employee_id=employee.id, trigger_type=RiskEventType.THRESHOLD_CROSSED, priority=priority,
                           status=status, latest_prediction_id=prediction.id, latest_score=Decimal(SENTINEL_SCORE),
                           latest_category=RiskCategory.HIGH, model_version=model.model_version, event_count=1,
                           first_detected_at=first_detected_at or created_at, last_observed_at=created_at,
                           acknowledged_at=acknowledged_at, resolved_at=resolved_at,
                           resolution_reason=SECRET_REVIEW if resolved_at else None, reviewer_notes=SECRET_REVIEW)
        record.created_at = created_at
        db.add(record)
        db.flush()
        db.add(RiskEvent(employee_id=employee.id, event_type=RiskEventType.THRESHOLD_CROSSED,
                         dedup_key=f"THRESHOLD_CROSSED:{prediction.id}", current_prediction_id=prediction.id,
                         current_score=Decimal(SENTINEL_SCORE), current_category=RiskCategory.HIGH,
                         model_version=model.model_version, detected_at=created_at, alert_id=record.id))
        db.commit()
        return record

    def intervention(employee, created_at=None, note=True):
        created_at = created_at or ago(3)
        record = Intervention(employee_id=employee.id, category=InterventionCategory.MANAGER_CHECK_IN,
                              title="Supportive check-in", description=SECRET_NOTE, priority=InterventionPriority.MEDIUM,
                              status=InterventionStatus.OPEN, version=2 if note else 1)
        db.add(record)
        db.flush()
        db.add(InterventionEvent(intervention_id=record.id, version=1, event_type=InterventionEventType.CREATED,
                                 created_at=created_at))
        if note:
            db.add(InterventionEvent(intervention_id=record.id, version=2, event_type=InterventionEventType.NOTE_ADDED,
                                     note=SECRET_NOTE, created_at=created_at + timedelta(minutes=5)))
        db.commit()
        return record

    def snapshot(day, departments: dict[int, int]):
        total = sum(departments.values())
        snap = WorkforceSnapshot(snapshot_date=day, captured_at=utcnow(), period_start=day.replace(day=1),
                                 total_employees=total, active_employees=total, on_leave_employees=0, inactive_employees=0,
                                 joiners_in_period=0, recorded_departures_in_period=0, source="test", data_quality={})
        db.add(snap)
        db.flush()
        for dept_id, active in departments.items():
            db.add(WorkforceSnapshotDepartment(snapshot_id=snap.id, department_id=dept_id, department_name="Dept",
                                               total_employees=active, active_employees=active, on_leave_employees=0,
                                               inactive_employees=0, joiners_in_period=0, recorded_departures_in_period=0))
        db.commit()

    return type("Add", (), {"attendance": staticmethod(attendance), "leave": staticmethod(leave),
                            "alert": staticmethod(alert), "intervention": staticmethod(intervention),
                            "snapshot": staticmethod(snapshot)})


def get(client, path, headers, **params):
    response = client.get(f"{BASE}{path}", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def all_items(client, headers, **params) -> list[dict]:
    items, page = [], 1
    while True:
        body = get(client, "/feed", headers, page=page, page_size=params.pop("page_size", 100), **params)
        items += body["items"]
        if page >= body["pages"]:
            return items
        page += 1


def evaluate_recommendations(client, headers) -> None:
    response = client.post("/api/recommendations/evaluate", json={}, headers=headers)
    assert response.status_code == 200, response.text


# --- Access ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/feed", "/summary", "/signals", "/sources", "/departments/1"])
def test_requires_authentication_and_denies_employees(client, org, path) -> None:
    assert client.get(f"{BASE}{path}").status_code == 401
    assert client.get(f"{BASE}{path}", headers=org["eng_h"]).status_code == 403


def test_manager_is_pinned_to_own_department(client, org, add) -> None:
    add.attendance(org["eng"])
    add.attendance(org["sales"])
    body = get(client, "/feed", org["manager_h"])
    assert body["scope"]["department_id"] == org["eng"].department_id
    assert {i["department_id"] for i in body["items"]} == {org["eng"].department_id}
    attendance = [i for i in body["items"] if i["category"] == "attendance"]
    assert [i["employee_id"] for i in attendance] == [org["eng"].id]
    sales_dept = org["sales"].department_id
    for path in ("/feed", "/summary", "/signals"):
        assert client.get(f"{BASE}{path}", headers=org["manager_h"], params={"department_id": sales_dept}).status_code == 404
    assert client.get(f"{BASE}/departments/{sales_dept}", headers=org["manager_h"]).status_code == 404
    assert client.get(f"{BASE}/departments/999", headers=org["hr"]).status_code == 404


def test_risk_items_about_the_caller_are_never_shown(client, org, add, db) -> None:
    add.alert(org["manager"])
    add.intervention(org["manager"])
    add.attendance(org["manager"])  # ordinary observed events about yourself are fine
    hr_categories = {i["category"] for i in all_items(client, org["hr"])}
    assert {"risk_alert", "risk_event", "intervention"} <= hr_categories
    manager_items = all_items(client, org["manager_h"])
    assert {i["category"] for i in manager_items} == {"attendance", "employee_record"}
    assert get(client, "/summary", org["manager_h"])["by_category"]["attendance"] == 1
    assert not set(get(client, "/summary", org["manager_h"])["by_category"]) & feed.SELF_EXCLUDED


# --- Normalization, timestamps and identity -------------------------------------------------------


def test_each_source_is_normalized_with_documented_identity(client, org, add) -> None:
    check_in = ago(30)
    att = add.attendance(org["eng"], created_at=ago(29), check_in=check_in)
    req = add.leave(org["eng"], created_at=ago(20), status=LeaveRequestStatus.CANCELLED, reviewed_at=ago(10),
                    cancelled_at=ago(4))
    alert = add.alert(org["eng2"], created_at=ago(8), status=AlertStatus.RESOLVED, acknowledged_at=ago(7),
                      resolved_at=ago(6))
    intervention = add.intervention(org["eng2"], created_at=ago(5))
    response = client.patch(f"/api/employees/{org['sales'].id}/status", headers=org["hr"],
                            json={"status": "INACTIVE", "reason": SECRET_REASON})
    assert response.status_code == 200, response.text

    items = {i["event_id"]: i for i in all_items(client, org["hr"])}
    assert items[f"attendance:{att.id}:checked_in"]["source_event_at"].startswith(
        check_in.replace(tzinfo=None).isoformat()[:19])
    assert items[f"attendance:{att.id}:checked_in"]["kind"] == "OBSERVED_EVENT"
    for action in ("submitted", "approved", "cancelled"):  # approval survives the later cancellation
        assert items[f"leave_request:{req.id}:{action}"]["source_event_at"] is None
    for action in ("created", "acknowledged", "resolved"):
        item = items[f"risk_alert:{alert.id}:{action}"]
        assert (item["kind"], item["priority"]) == ("RISK_EVENT", "HIGH")
    assert any(e.startswith("risk_event:") for e in items)
    notes = [i for i in items.values() if i["category"] == "intervention"]
    assert {i["action"] for i in notes} == {"created", "note_added"}
    assert all(i["source_id"] and i["employee_id"] == intervention.employee_id for i in notes)
    status = [i for i in items.values() if i["category"] == "employee_status"]
    assert len(status) == 1 and status[0]["description"] == "Employment status changed from ACTIVE to INACTIVE"
    assert status[0]["source_event_at"] is None and "may lag" in status[0]["timestamp_basis"]
    created = [i for i in items.values() if i["category"] == "employee_record"]
    assert {i["source_event_at"] for i in created} == {"2024-01-01"}  # date of joining, not creation time
    # Audit rows for leave/alert actions are not listed again: the only audit-sourced items are status changes.
    assert all(i["source_type"] != "audit_logs" or i["category"] == "employee_status" for i in items.values())
    assert len(items) == len(all_items(client, org["hr"]))  # identities are unique


def test_ordering_is_deterministic_with_equal_timestamps(client, org, add) -> None:
    same = ago(3)
    records = [add.attendance(org["eng"], created_at=same) for _ in range(12)]
    add.attendance(org["eng"], created_at=ago(1))
    items = all_items(client, org["hr"], category="attendance")
    keys = [(i["recorded_at"], i["event_id"]) for i in items]
    assert keys == sorted(keys, reverse=True)
    assert len({i["event_id"] for i in items}) == 13
    paged = [i["event_id"] for p in (1, 2, 3, 4, 5) for i in
             get(client, "/feed", org["hr"], category="attendance", page=p, page_size=3)["items"]]
    assert paged == [i["event_id"] for i in items]  # pages never overlap or skip
    assert paged == [i["event_id"] for i in all_items(client, org["hr"], category="attendance")]  # repeatable
    assert {f"attendance:{r.id}:checked_in" for r in records} <= set(paged)


def test_window_is_organization_local_and_inclusive(client, org, add) -> None:
    tz = get_settings().org_tz
    yesterday = today() - timedelta(days=1)
    inside = add.attendance(org["eng"], created_at=datetime.combine(yesterday, time(0, 0, 1), tzinfo=tz))
    before = add.attendance(org["eng"], created_at=datetime.combine(yesterday, time.min, tzinfo=tz) - timedelta(seconds=1))
    ids = {i["event_id"] for i in all_items(client, org["hr"], date_from=yesterday.isoformat(), date_to=yesterday.isoformat())}
    assert f"attendance:{inside.id}:checked_in" in ids and f"attendance:{before.id}:checked_in" not in ids
    old = add.attendance(org["eng"], created_at=ago(24 * 10))
    assert f"attendance:{old.id}:checked_in" not in {i["event_id"] for i in all_items(client, org["hr"])}  # default 7 days


# --- Filters and validation ------------------------------------------------------------------------


def test_category_kind_and_priority_filters(client, org, add) -> None:
    add.attendance(org["eng"])
    add.alert(org["eng2"], priority=AlertPriority.LOW)
    add.alert(org["eng"], priority=AlertPriority.HIGH)
    assert {i["category"] for i in all_items(client, org["hr"], kind="RISK_EVENT")} == {"risk_alert", "risk_event"}
    assert {i["category"] for i in all_items(client, org["hr"], category=["attendance", "risk_event"])} == {"attendance", "risk_event"}
    high = all_items(client, org["hr"], priority="HIGH")
    assert high and {i["priority"] for i in high} == {"HIGH"}


@pytest.mark.parametrize("params", [
    {"category": "payroll"}, {"kind": "PREDICTION"}, {"priority": "URGENT"}, {"page": 0}, {"page_size": 1000},
    {"department_id": 0}, {"date_from": "2026-02-30"}, {"unknown": "1"}, {"employee_id": "1"},
])
def test_invalid_filters_are_rejected(client, org, params) -> None:
    response = client.get(f"{BASE}/feed", headers=org["hr"], params=params)
    assert response.status_code == 422
    for value in params.values():
        assert str(value) not in response.text or str(value) in ("0", "1")  # input is not echoed


def test_date_window_bounds(client, org) -> None:
    max_days = get_settings().activity_feed_max_window_days
    start = today() - timedelta(days=max_days)
    for path in ("/feed", "/summary", f"/departments/{org['eng'].department_id}"):
        too_long = client.get(f"{BASE}{path}", headers=org["hr"], params={"date_from": start.isoformat(), "date_to": today().isoformat()})
        assert too_long.status_code == 422 and str(max_days) in too_long.text
        reversed_ = client.get(f"{BASE}{path}", headers=org["hr"],
                               params={"date_from": today().isoformat(), "date_to": (today() - timedelta(days=1)).isoformat()})
        assert reversed_.status_code == 422
    ok = get(client, "/feed", org["hr"], date_from=(start + timedelta(days=1)).isoformat(), date_to=today().isoformat())
    assert ok["window"] == {"from": (start + timedelta(days=1)).isoformat(), "to": today().isoformat()}
    conflict = client.get(f"{BASE}/departments/{org['eng'].department_id}", headers=org["hr"],
                          params={"department_id": org["sales"].department_id})
    assert conflict.status_code == 422


# --- Summary consistency ------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["hr", "manager_h"])
@pytest.mark.parametrize("params", [{}, {"category": "leave"}, {"kind": "RISK_EVENT"}, {"priority": "HIGH"}])
def test_summary_counts_match_the_feed(client, org, add, who, params) -> None:
    add.attendance(org["eng"])
    add.attendance(org["sales"])
    add.leave(org["eng"], status=LeaveRequestStatus.REJECTED, reviewed_at=ago(1))
    add.alert(org["eng2"])
    add.alert(org["sales"], priority=AlertPriority.MEDIUM)
    add.intervention(org["eng2"])
    items = all_items(client, org[who], **params)
    summary = get(client, "/summary", org[who], **params)
    assert summary["total"] == len(items) == get(client, "/feed", org[who], **params)["total"]
    counted: dict[str, int] = {}
    for i in items:
        counted[i["category"]] = counted.get(i["category"], 0) + 1
    assert summary["by_category"] == counted
    assert sum(summary["by_kind"].values()) == sum(summary["by_action"].values()) == len(items)


# --- Privacy ---------------------------------------------------------------------------------------------


def test_no_sensitive_content_or_names_leak(client, org, add) -> None:
    add.attendance(org["eng"])
    add.leave(org["eng"], status=LeaveRequestStatus.APPROVED, reviewed_at=ago(1))
    add.alert(org["eng2"], status=AlertStatus.RESOLVED, resolved_at=ago(1), first_detected_at=ago(24 * 30))
    add.intervention(org["eng2"])
    evaluate_recommendations(client, org["hr"])
    dept = org["eng"].department_id
    bodies = [client.get(f"{BASE}{p}", headers=h).text
              for h in (org["hr"], org["manager_h"])
              for p in ("/feed", "/summary", "/signals", "/sources", f"/departments/{dept}")]
    forbidden = [SECRET_REASON, SECRET_NOTE, SECRET_REVIEW, SENTINEL_SCORE, "987", "654.5", "risk_score",
                 "latest_score", "features", "Edith", "Ezra", "Mona", "Sam Seller", "@corp.example.com", "EMP0",
                 "salary", "net_pay", "gross"]
    for body in bodies:
        for word in forbidden:
            assert word not in body, word


def test_recommendations_listed_within_scope_without_text(client, org, add, db) -> None:
    add.alert(org["eng2"])
    add.alert(org["sales"])
    evaluate_recommendations(client, org["hr"])
    hr_recs = [i for i in all_items(client, org["hr"]) if i["category"] == "recommendation"]
    manager_recs = [i for i in all_items(client, org["manager_h"]) if i["category"] == "recommendation"]
    assert hr_recs and {i["action"] for i in hr_recs} == {"generated"}
    assert manager_recs and {i["department_id"] for i in manager_recs} == {org["eng"].department_id}
    assert len(manager_recs) < len(hr_recs)
    body = client.get(f"{BASE}/feed", headers=org["hr"]).text
    for rec in db.scalars(select(Recommendation)):  # rule-generated text stays in the recommendations module
        assert rec.title not in body and rec.explanation not in body and rec.suggested_action not in body


# --- Read-only and query bounds ----------------------------------------------------------------------


def test_reading_never_writes(client, org, add, db) -> None:
    add.attendance(org["eng"])
    add.alert(org["eng2"], first_detected_at=ago(24 * 40))
    add.intervention(org["eng2"])
    tables = (AuditLog, Notification, Recommendation, Intervention, InterventionEvent, RiskAlert, RiskEvent,
              WorkforceSnapshot, Attendance, LeaveRequest, Employee)

    def counts():
        db.expire_all()
        return {t.__tablename__: db.scalar(select(func.count()).select_from(t)) for t in tables}

    before = counts()
    dept = org["eng"].department_id
    for headers in (org["hr"], org["admin"], org["manager_h"]):
        for path in ("/feed", "/summary", "/signals", "/sources", f"/departments/{dept}"):
            assert client.get(f"{BASE}{path}", headers=headers).status_code == 200
    assert counts() == before


def test_query_count_does_not_grow_with_records(client, org, add, count_queries) -> None:
    dept = org["eng"].department_id

    def measure() -> dict[str, int]:
        result = {}
        for path in ("/feed", "/summary", "/signals", f"/departments/{dept}"):
            start = len(count_queries())
            assert client.get(f"{BASE}{path}", headers=org["hr"]).status_code == 200
            result[path] = len(count_queries()) - start
        return result

    add.attendance(org["eng"])
    add.alert(org["eng2"])
    add.intervention(org["eng2"])
    small = measure()
    for _ in range(6):
        add.attendance(org["eng2"])
        add.attendance(org["sales"])
        add.leave(org["eng"], status=LeaveRequestStatus.APPROVED, reviewed_at=ago(1))
        add.alert(org["sales"])
        add.intervention(org["eng"])
    assert measure() == small


def test_signal_query_count_does_not_grow_with_departments(client, org, add, staff, count_queries) -> None:
    def measure() -> int:
        start = len(count_queries())
        assert client.get(f"{BASE}/signals", headers=org["hr"]).status_code == 200
        return len(count_queries()) - start

    add.snapshot(today() - timedelta(days=1), {org["eng"].department_id: 3})
    small = measure()
    for n in range(5):
        employee, _ = staff(department=f"Extra {n}")
        add.attendance(employee)
        add.snapshot(today() - timedelta(days=2 + n), {employee.department_id: 1})
    assert measure() == small


def test_truncation_is_reported(db, org, add) -> None:
    for _ in range(4):
        add.attendance(org["eng"])
    scope = feed.Scope(None, 0, True)
    start, end = ago(48), utcnow() + timedelta(hours=1)
    result = feed.collect(db, scope, start, end, {"attendance"}, None, None, cap=3)
    assert result.truncated and result.sources_truncated == ["attendance"] and len(result.items) == 3
    full = feed.collect(db, scope, start, end, {"attendance"}, None, None, cap=100)
    assert not full.truncated and [i.event_id for i in full.items][:3] == [i.event_id for i in result.items]


# --- Department activity and signals -------------------------------------------------------------------


def test_department_activity_counts(client, org, add) -> None:
    dept = org["eng"].department_id
    empty = get(client, f"/departments/{dept}", org["hr"])["activity"]
    assert empty["attendance"]["status"] == "unavailable" and empty["attendance"]["records"] is None
    assert empty["leave"]["approval_rate"] is None
    add.attendance(org["eng"], status=AttendanceStatus.LATE)
    add.attendance(org["eng2"])
    add.leave(org["eng"], status=LeaveRequestStatus.APPROVED, reviewed_at=ago(1))
    add.leave(org["eng"], status=LeaveRequestStatus.CANCELLED, reviewed_at=ago(2), cancelled_at=ago(1))
    add.leave(org["eng2"], status=LeaveRequestStatus.REJECTED, reviewed_at=ago(1))
    add.leave(org["sales"], status=LeaveRequestStatus.REJECTED, reviewed_at=ago(1))
    body = get(client, f"/departments/{dept}", org["manager_h"])
    activity = body["activity"]
    assert activity["attendance"]["records"] == 2 and activity["attendance"]["records_by_status"]["LATE"] == 1
    assert activity["leave"] | {"approval_rate_definition": None} == {
        "submitted": 3, "approved": 2, "rejected": 1, "cancelled": 1, "approval_rate": 0.6667, "approval_rate_definition": None}
    assert activity["active_employees_now"] == 3
    assert all(s["department_id"] == dept for s in body["signals"])


def test_signals_statuses_and_scope(client, org, add, db) -> None:
    eng, sales = org["eng"].department_id, org["sales"].department_id
    add.attendance(org["eng"])
    add.alert(org["sales"], first_detected_at=ago(24 * 30))  # awaiting review
    add.alert(org["eng2"], first_detected_at=ago(24 * 2))  # recent
    signals = {(s["signal_id"], s["department_id"]): s for s in get(client, "/signals", org["hr"])["signals"]}
    assert signals[("SNAPSHOT_STALE", None)]["status"] == "ACTIVE"
    assert signals[("ATTENDANCE_DATA_GAP", eng)]["status"] == "CLEAR"
    assert signals[("ATTENDANCE_DATA_GAP", sales)]["status"] == "ACTIVE"
    assert signals[("HEADCOUNT_DECLINE", eng)]["status"] == "INSUFFICIENT_DATA"
    assert signals[("ALERTS_AWAITING_REVIEW", sales)]["evidence"]["count"] == 1
    assert signals[("ALERTS_AWAITING_REVIEW", eng)]["status"] == "CLEAR"
    assert {s["kind"] for s in signals.values()} == {"AGGREGATE_SIGNAL", "DATA_QUALITY_SIGNAL"}
    assert all("employee_id" not in s and "employee_id" not in s["evidence"] for s in signals.values())

    manager = get(client, "/signals", org["manager_h"])
    assert {s["department_id"] for s in manager["signals"]} == {eng}
    assert "not predictions of resignation" in manager["note"]


def test_headcount_decline_signal_from_month_end_snapshots(client, org, add) -> None:
    eng = org["eng"].department_id
    first = today().replace(day=1)
    month_ends = []
    for back in range(4, 0, -1):
        day = first
        for _ in range(back):
            day = (day - timedelta(days=1)).replace(day=1)
        month_ends.append((day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
    for day, active in zip(month_ends, (20, 18, 16, 15)):
        add.snapshot(day, {eng: active})
    signal = next(s for s in get(client, "/signals", org["hr"])["signals"]
                  if s["signal_id"] == "HEADCOUNT_DECLINE" and s["department_id"] == eng)
    assert signal["status"] == "ACTIVE" and signal["evidence"]["last_four_month_ends"] == [20, 18, 16, 15]


def test_sources_catalogue(client, org) -> None:
    body = get(client, "/sources", org["manager_h"])
    assert {c["category"] for c in body["categories"]} == set(feed.SOURCES)
    assert "payroll" in body["excluded"] and "not a live stream" in body["note"]
