"""Workforce analytics API. Snapshot history and audit records here are SYNTHETIC test data."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.main import app
from app.models import (
    AlertPriority,
    AlertStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Department,
    Employee,
    EmploymentStatus,
    ModelVersionStatus,
    RiskAlert,
    RiskCategory,
    RiskEventType,
    UserRole,
    WorkforceSnapshot,
    WorkforceSnapshotDepartment,
)
from app.models.base import utcnow
from ml.attrition.features import FEATURE_SCHEMA_VERSION
from tests.conftest import local

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
TODAY = date(2026, 10, 15)
BASE = "/api/workforce-analytics"


@pytest.fixture
def fixed_today(monkeypatch):
    from app.api import workforce_analytics as api

    monkeypatch.setattr(api, "utcnow", lambda: local(2026, 10, 15, 10, 0))
    return TODAY


def add_snapshot(db, day: date, departments: dict[Department, tuple[int, int, int]]) -> WorkforceSnapshot:
    """departments: {department: (active, on_leave, inactive)}. Org totals are the sums."""
    active = sum(v[0] for v in departments.values())
    on_leave = sum(v[1] for v in departments.values())
    inactive = sum(v[2] for v in departments.values())
    snapshot = WorkforceSnapshot(snapshot_date=day, captured_at=utcnow(), period_start=day.replace(day=1),
                                 total_employees=active + on_leave + inactive, active_employees=active,
                                 on_leave_employees=on_leave, inactive_employees=inactive, joiners_in_period=0,
                                 recorded_departures_in_period=0, source="synthetic_test", data_quality={})
    db.add(snapshot)
    db.flush()
    for dept, (a, l, i) in departments.items():
        db.add(WorkforceSnapshotDepartment(snapshot_id=snapshot.id, department_id=dept.id, department_name=dept.name,
                                           total_employees=a + l + i, active_employees=a, on_leave_employees=l,
                                           inactive_employees=i, joiners_in_period=0, recorded_departures_in_period=0))
    db.commit()
    return snapshot


def add_departure(db, employee: Employee, when: datetime, to="INACTIVE", frm="ACTIVE") -> None:
    db.add(AuditLog(action="employee.status_changed", entity_type="employee", entity_id=str(employee.id),
                    details={"changes": {"status": {"from": frm, "to": to}}}, created_at=when))
    db.commit()


def month_end(year: int, month: int) -> date:
    return (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))


@pytest.fixture
def org(staff, make_department):
    manager, manager_h = staff(MANAGER, department="Engineering")
    eng = [staff(department="Engineering")[0] for _ in range(3)]
    sales_emp, _ = staff(department="Sales")
    on_leave, _ = staff(department="Sales", status=EmploymentStatus.ON_LEAVE)
    empty = make_department("Research")
    return {"manager": manager, "manager_h": manager_h, "eng": eng, "sales": sales_emp, "on_leave": on_leave,
            "empty": empty}


def departments(db) -> dict[str, Department]:
    return {d.name: d for d in db.scalars(select(Department))}


# --- Snapshot capture ---------------------------------------------------------------------------


def test_capture_counts_and_departments(client, db, org, headers_for) -> None:
    hr = headers_for(HR)
    leaver = org["eng"][0]
    client.patch(f"/api/employees/{leaver.id}/status", json={"status": "INACTIVE", "reason": "Resigned"}, headers=hr)

    response = client.post(f"{BASE}/snapshots/capture", json={}, headers=hr)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["total_employees"], body["active_employees"], body["on_leave_employees"], body["inactive_employees"]) == (6, 4, 1, 1)
    assert body["recorded_departures_in_period"] == 1
    assert body["data_quality"]["inactive_without_recorded_departure"] == 0
    rows = {d["department_name"]: d for d in body["departments"]}
    assert rows["Engineering"]["active_employees"] == 3 and rows["Engineering"]["recorded_departures_in_period"] == 1
    assert rows["Research"]["total_employees"] == 0  # recorded as zero, not omitted
    for leak in (leaver.full_name, leaver.work_email, leaver.employee_code, "salary"):
        assert leak not in response.text

    log = db.scalar(select(AuditLog).where(AuditLog.action == "workforce.snapshot_captured"))
    assert set(log.details) == {"snapshot_date", "total_employees", "departments"}


def test_capture_is_once_per_day_and_immutable(client, db, org, headers_for) -> None:
    hr = headers_for(HR)
    first = client.post(f"{BASE}/snapshots/capture", json={}, headers=hr).json()
    again = client.post(f"{BASE}/snapshots/capture", json={}, headers=hr)
    assert again.status_code == 409 and "immutable" in again.json()["detail"]
    assert db.scalar(select(func.count(WorkforceSnapshot.id))) == 1
    assert client.delete(f"{BASE}/snapshots/{first['id']}", headers=hr).status_code in (404, 405)
    assert client.patch(f"{BASE}/snapshots/{first['id']}", json={}, headers=hr).status_code in (404, 405)


@pytest.mark.parametrize("day", ["2020-01-01", "2099-01-01"])
def test_capture_refuses_other_dates(client, org, headers_for, day) -> None:
    response = client.post(f"{BASE}/snapshots/capture", json={"snapshot_date": day}, headers=headers_for(ADMIN))
    assert response.status_code == 422 and "only be captured for today" in response.json()["detail"]


def test_capture_flags_unknown_departure_dates(client, staff, headers_for) -> None:
    staff(status=EmploymentStatus.INACTIVE)  # inactive without any recorded status change
    body = client.post(f"{BASE}/snapshots/capture", json={}, headers=headers_for(HR)).json()
    assert body["data_quality"]["inactive_without_recorded_departure"] == 1
    assert body["recorded_departures_in_period"] == 0  # not invented from the INACTIVE count
    assert body["data_quality"]["warnings"]


@pytest.mark.simulated_concurrent_writer
def test_concurrent_capture_conflict(client, db, org, headers_for, monkeypatch, session_factory) -> None:
    from app.services import workforce_analytics as service

    original = service.current_counts

    def capture_elsewhere_first(*args, **kwargs):
        with session_factory() as other:
            other.add(WorkforceSnapshot(snapshot_date=utcnow().astimezone(get_settings().org_tz).date(),  # org-local today
                                        captured_at=utcnow(), period_start=date(2000, 1, 1), total_employees=0,
                                        active_employees=0, on_leave_employees=0, inactive_employees=0,
                                        joiners_in_period=0, recorded_departures_in_period=0, source="other",
                                        data_quality={}))
            other.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "current_counts", capture_elsewhere_first)
    response = client.post(f"{BASE}/snapshots/capture", json={}, headers=headers_for(HR))
    assert response.status_code == 409
    assert db.scalar(select(func.count(WorkforceSnapshot.id))) == 1


def test_capture_audit_failure_rolls_back(client, org, headers_for, monkeypatch, session_factory) -> None:
    from app.api import workforce_analytics as api

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(f"{BASE}/snapshots/capture", json={}, headers=headers_for(HR))
    assert response.status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(WorkforceSnapshot.id))) == 0
        assert fresh.scalar(select(func.count(WorkforceSnapshotDepartment.id))) == 0


def test_snapshot_list_filters_and_pagination(client, db, org, headers_for) -> None:
    d = departments(db)
    for month in (7, 8, 9):
        add_snapshot(db, month_end(2026, month), {d["Engineering"]: (3, 0, 0)})
    hr = headers_for(HR)
    page = client.get(f"{BASE}/snapshots", params={"page_size": 2}, headers=hr).json()
    assert (page["total"], page["pages"], page["items"][0]["snapshot_date"]) == (3, 2, "2026-09-30")
    filtered = client.get(f"{BASE}/snapshots", params={"date_from": "2026-08-01"}, headers=hr).json()
    assert filtered["total"] == 2
    assert client.get(f"{BASE}/snapshots", params={"date_from": "2026-09-01", "date_to": "2026-08-01"}, headers=hr).status_code == 422


# --- Trends and forecast ------------------------------------------------------------------------


@pytest.fixture
def history(db, org):
    """Month-end snapshots Jan-Sep 2026 with a gap in April. Engineering grows by 1 per month."""
    d = departments(db)
    for month in (1, 2, 3, 5, 6, 7, 8, 9):
        add_snapshot(db, month_end(2026, month), {d["Engineering"]: (9 + month, 0, 0), d["Sales"]: (5, 1, 0)})
    return d


def test_trends_show_gaps_without_filling(client, history, headers_for, fixed_today) -> None:
    body = client.get(f"{BASE}/trends", params={"date_from": "2026-01-01", "date_to": "2026-09-30"},
                      headers=headers_for(HR)).json()["data"]
    months = {m["month"]: m for m in body["monthly"]}
    assert months["2026-04"]["observed"] is False and months["2026-04"]["active_employees"] is None
    assert months["2026-03"]["active_employees"] == 17  # Engineering 12 + Sales 5
    assert months["2026-05"]["turnover_rate"] is None  # April missing: no start-of-month denominator
    assert months["2026-03"]["turnover_rate"] == 0.0
    assert body["yearly"][0]["months_observed"] == 8 and body["yearly"][0]["months_in_range"] == 9


def test_forecast_with_enough_history(client, history, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    engineering = history["Engineering"].id
    data = client.get(f"{BASE}/forecast", params={"department_id": engineering, "horizon_months": 3}, headers=hr).json()["data"]
    assert data["status"] == "ok" and data["observed_months"] == 8 and data["monthly_trend"] == 1.0
    assert [p["projected_active_headcount"] for p in data["projections"]] == [19, 20, 21]
    assert data["projections"][0]["month"] == "2026-10"
    assert data["projected_net_change"] == 3
    assert data["backtest_mean_absolute_error"] is None  # 8 < 6 + 3 observations
    assert "not a prediction of which employees will leave" in data["note"]


def test_forecast_insufficient_history_and_limits(client, db, org, headers_for, fixed_today) -> None:
    d = departments(db)
    for month in (8, 9):
        add_snapshot(db, month_end(2026, month), {d["Engineering"]: (3, 0, 0)})
    hr = headers_for(HR)
    data = client.get(f"{BASE}/forecast", headers=hr).json()["data"]
    assert (data["status"], data["projections"], data["observed_months"]) == ("insufficient_history", [], 2)
    assert client.get(f"{BASE}/forecast", params={"horizon_months": 13}, headers=hr).status_code == 422  # setting max 12
    assert client.get(f"{BASE}/forecast", params={"horizon_months": 0}, headers=hr).status_code == 422
    assert client.get(f"{BASE}/forecast", params={"horizon_months": 37}, headers=hr).status_code == 422


def test_trend_range_validation(client, org, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    assert client.get(f"{BASE}/trends", params={"date_from": "2020-01-01", "date_to": "2026-01-01"}, headers=hr).status_code == 422
    assert client.get(f"{BASE}/trends", params={"date_from": "2027-01-01"}, headers=hr).status_code == 422
    assert client.get(f"{BASE}/trends", params={"department_id": 999}, headers=hr).status_code == 404
    assert client.get(f"{BASE}/trends", params={"employee_id": 1}, headers=hr).status_code == 422


# --- Stability and retention -------------------------------------------------------------------


def test_stability_statuses_and_score(client, db, org, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    d = departments(db)
    assert client.get(f"{BASE}/stability", headers=hr).json()["data"]["status"] == "insufficient_data"

    add_snapshot(db, date(2026, 6, 30), {d["Engineering"]: (100, 0, 0)})
    add_snapshot(db, date(2026, 7, 31), {d["Engineering"]: (100, 0, 0)})
    add_snapshot(db, date(2026, 8, 25), {d["Engineering"]: (100, 0, 0)})
    stale = client.get(f"{BASE}/stability", headers=hr).json()["data"]
    assert (stale["status"], stale["score"]) == ("stale_data", None)  # latest is 51 days old

    add_snapshot(db, date(2026, 10, 10), {d["Engineering"]: (100, 0, 0)})
    ok = client.get(f"{BASE}/stability", headers=hr).json()["data"]
    assert ok["status"] == "ok" and ok["score"] == 100.0
    assert ok["recorded_departures_in_window"] == 0 and ok["components"]["turnover"] == 100.0
    assert "0.5*turnover" in ok["formula"]
    assert client.get(f"{BASE}/stability", headers=hr).json() == client.get(f"{BASE}/stability", headers=hr).json()


def test_retention_observed_vs_ai_indicators(client, db, org, headers_for, fixed_today, staff) -> None:
    d = departments(db)
    eng = org["eng"]
    add_snapshot(db, date(2026, 6, 30), {d["Engineering"]: (4, 0, 0), d["Sales"]: (2, 0, 0)})
    add_snapshot(db, date(2026, 9, 30), {d["Engineering"]: (4, 0, 0), d["Sales"]: (2, 0, 0)})
    add_departure(db, eng[0], local(2026, 8, 3, 12))
    add_departure(db, eng[1], local(2026, 8, 20, 12), to="ACTIVE", frm="INACTIVE")  # reactivation
    add_departure(db, eng[2], local(2026, 8, 21, 12), to="ON_LEAVE", frm="ACTIVE")  # not a departure

    # Stored predictions and a risk alert: these must never count as departures.
    model = AttritionModelVersion(model_version="lr-20260101000000-0000abcd", feature_schema_version=FEATURE_SCHEMA_VERSION,
                                  status=ModelVersionStatus.ACTIVE, trained_at=utcnow(), dataset_fingerprint="0" * 64,
                                  artifact_sha256="0" * 64, training_rows=100, evaluation={})
    db.add(model)
    db.flush()
    for emp, score in ((eng[1], "82"), (eng[2], "35"), (org["manager"], "90")):
        db.add(AttritionPrediction(employee_id=emp.id, model_version_id=model.id, feature_schema_version=FEATURE_SCHEMA_VERSION,
                                   risk_score=Decimal(score), risk_category=RiskCategory.HIGH if Decimal(score) >= 70 else RiskCategory.LOW,
                                   features={}, predicted_at=utcnow()))
    db.flush()
    pred_id = db.scalar(select(AttritionPrediction.id).where(AttritionPrediction.employee_id == eng[1].id))
    db.add(RiskAlert(employee_id=eng[1].id, trigger_type=RiskEventType.THRESHOLD_CROSSED, priority=AlertPriority.HIGH,
                     status=AlertStatus.OPEN, latest_prediction_id=pred_id, latest_score=Decimal("82"),
                     latest_category=RiskCategory.HIGH, model_version=model.model_version, event_count=1,
                     first_detected_at=utcnow(), last_observed_at=utcnow()))
    db.commit()

    response = client.get(f"{BASE}/retention", params={"date_from": "2026-06-01", "date_to": "2026-10-15"},
                          headers=headers_for(HR))
    data = response.json()["data"]
    observed, ai = data["observed"], data["ai_risk_indicators"]
    assert (observed["recorded_departures"], observed["reactivations"]) == (1, 1)
    assert observed["turnover_rate"] == round(1 / 6, 4)
    assert observed["turnover_denominator"] == {"snapshots_in_window": 2, "average_active_headcount": 6.0}
    assert ai["latest_prediction_by_category"] == {"LOW": 1, "MEDIUM": 0, "HIGH": 2}
    assert ai["risk_alerts_by_status"]["OPEN"] == 1
    assert "not observed departures" in ai["note"]
    for leak in (eng[1].full_name, eng[1].employee_code, "features", "risk_score", "salary"):
        assert leak not in response.text

    # A manager's own prediction is excluded from the aggregates they see.
    manager_view = client.get(f"{BASE}/retention", params={"date_from": "2026-06-01", "date_to": "2026-10-15"},
                              headers=org["manager_h"]).json()["data"]["ai_risk_indicators"]
    assert manager_view["latest_prediction_by_category"]["HIGH"] == 1


def test_turnover_unavailable_without_snapshots(client, db, org, headers_for, fixed_today) -> None:
    add_departure(db, org["eng"][0], local(2026, 9, 3, 12))
    observed = client.get(f"{BASE}/retention", headers=headers_for(HR)).json()["data"]["observed"]
    assert observed["recorded_departures"] == 1
    assert (observed["turnover_rate"], observed["turnover_status"]) == (None, "unavailable")
    assert "2 snapshots" in observed["turnover_reason"]


def test_overview_live_counts(client, db, org, headers_for, fixed_today) -> None:
    data = client.get(f"{BASE}/overview", headers=headers_for(HR)).json()["data"]
    assert data["current"] == {"total_employees": 6, "active_employees": 5, "on_leave_employees": 1, "inactive_employees": 0}
    assert {d["department_name"] for d in data["by_department"]} == {"Engineering", "Sales", "Research"}
    assert data["snapshots"] == {"count": 0, "latest_snapshot_date": None}


# --- Permissions and isolation ----------------------------------------------------------------------


def test_manager_is_pinned_to_own_department(client, db, org, history, fixed_today) -> None:
    headers = org["manager_h"]
    engineering, sales = history["Engineering"].id, history["Sales"].id
    data = client.get(f"{BASE}/overview", headers=headers).json()
    assert data["scope"] == {"level": "department", "department_id": engineering}
    assert [d["department_name"] for d in data["data"]["by_department"]] == ["Engineering"]
    assert data["data"]["current"]["total_employees"] == 4

    for path in ("/overview", "/trends", "/forecast", "/stability", "/retention"):
        other = client.get(f"{BASE}{path}", params={"department_id": sales}, headers=headers)
        missing = client.get(f"{BASE}{path}", params={"department_id": 999}, headers=headers)
        assert other.status_code == missing.status_code == 404, path
        assert other.json() == missing.json()  # existence does not leak
    assert client.get(f"{BASE}/departments/{sales}", headers=headers).status_code == 404
    assert client.get(f"{BASE}/departments/{engineering}", headers=headers).status_code == 200
    assert client.post(f"{BASE}/snapshots/capture", json={}, headers=headers).status_code == 403
    assert client.get(f"{BASE}/snapshots", headers=headers).status_code == 403


def test_manager_without_department_and_employees(client, headers_for, staff, fixed_today) -> None:
    assert client.get(f"{BASE}/overview", headers=headers_for(MANAGER)).status_code == 403
    _, employee_h = staff()
    for method, path in [("post", "/snapshots/capture"), ("get", "/snapshots"), ("get", "/overview"), ("get", "/trends"),
                         ("get", "/forecast"), ("get", "/stability"), ("get", "/retention"), ("get", "/departments/1")]:
        kwargs = {"json": {}} if method == "post" else {}
        assert getattr(client, method)(f"{BASE}{path}", headers=employee_h, **kwargs).status_code == 403, path


def test_department_detail_for_hr(client, history, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    body = client.get(f"{BASE}/departments/{history['Sales'].id}", headers=hr).json()
    assert set(body["data"]) == {"overview", "forecast", "stability", "retention"}
    assert body["data"]["forecast"]["status"] == "ok"
    assert [p["projected_active_headcount"] for p in body["data"]["forecast"]["projections"]][:2] == [5, 5]
    assert client.get(f"{BASE}/departments/999", headers=hr).status_code == 404


def test_analytics_never_mutate_records(client, db, org, history, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    before = (db.scalar(select(func.count(Employee.id))), db.scalar(select(func.count(AuditLog.id))),
              db.scalar(select(func.count(AttritionPrediction.id))))
    for path in ("/overview", "/trends", "/forecast", "/stability", "/retention"):
        assert client.get(f"{BASE}{path}", headers=hr).status_code == 200
    db.expire_all()
    after = (db.scalar(select(func.count(Employee.id))), db.scalar(select(func.count(AuditLog.id))),
             db.scalar(select(func.count(AttritionPrediction.id))))
    assert before == after  # reads create no audit rows, predictions or employee changes


def test_endpoints_require_authentication(client) -> None:
    for path in ("/overview", "/trends", "/forecast", "/stability", "/retention", "/snapshots", "/departments/1"):
        assert client.get(f"{BASE}{path}").status_code == 401
    assert client.post(f"{BASE}/snapshots/capture", json={}).status_code == 401
