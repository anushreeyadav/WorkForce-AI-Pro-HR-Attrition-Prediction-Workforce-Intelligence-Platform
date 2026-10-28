"""Stability engine hardening (Sub-prompt 27): population threshold, department comparison, forecast freshness,
whole-month trend rows, recommendation integration and privacy. Synthetic records in in-memory SQLite only."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Department, EmploymentStatus, Recommendation, UserRole, WorkforceSnapshot, WorkforceSnapshotDepartment
from app.models.base import utcnow
from app.services import workforce_analytics as service
from tests.conftest import local
from tests.test_workforce_analytics_api import (  # noqa: F401  (pytest fixtures)
    BASE,
    add_departure,
    add_snapshot,
    departments,
    fixed_today,
    history,
    month_end,
    org,
)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE


def get(client, path, headers, expected=200, **params):
    response = client.get(f"{BASE}{path}", headers=headers, params=params)
    assert response.status_code == expected, response.text
    return response.json()


# --- Stability: small populations and the department comparison ------------------------------------------------------


def test_small_department_is_reported_as_insufficient_data(client, history, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    sales = get(client, "/stability", hr, department_id=history["Sales"].id)["data"]  # 5 active every month
    assert (sales["status"], sales["reason_code"], sales["score"]) == ("insufficient_data", "small_population", None)
    assert sales["metrics"]["average_active_headcount"] == 5.0 and sales["minimum_average_headcount"] == 10
    engineering = get(client, "/stability", hr, department_id=history["Engineering"].id)["data"]  # 10..18 active
    assert engineering["status"] == "ok" and engineering["score"] is not None
    organization = get(client, "/stability", hr)["data"]
    assert organization["status"] == "ok" and organization["latest_snapshot_date"] == "2026-09-30"
    assert "not a validated prediction" in organization["note"]


def test_department_comparison_matches_each_department_alone(client, db, org, history, headers_for, fixed_today) -> None:
    add_departure(db, org["eng"][0], local(2026, 7, 10, 12))  # one recorded Engineering departure in the window
    hr = headers_for(HR)
    body = get(client, "/stability", hr, by_department="true")["data"]
    entries = {e["department_name"]: e for e in body["departments"]}
    assert list(entries) == ["Engineering", "Research", "Sales"]  # every department, sorted by name
    assert entries["Research"]["reason_code"] == "too_few_snapshots"  # no snapshot rows: never "stable" by default
    assert entries["Engineering"]["recorded_departures_in_window"] == 1
    for name, entry in entries.items():
        alone = get(client, "/stability", hr, department_id=entry["department_id"])["data"]
        for key in ("status", "reason_code", "score", "components", "metrics", "latest_snapshot_date",
                    "recorded_departures_in_window"):
            assert entry[key] == alone[key], (name, key)
    assert "departments" not in get(client, "/stability", hr)["data"]  # opt-in; the default response is unchanged


def test_department_comparison_is_scoped(client, org, history, headers_for, fixed_today, staff) -> None:
    manager = org["manager_h"]
    own = get(client, "/stability", manager, by_department="true")["data"]["departments"]
    assert [e["department_name"] for e in own] == ["Engineering"]
    get(client, "/stability", manager, 404, department_id=history["Sales"].id, by_department="true")
    _, employee = staff()
    get(client, "/stability", employee, 403, by_department="true")
    get(client, "/stability", headers_for(HR), 422, by_department="maybe")


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_stability_and_forecast_expose_aggregates_only(client, org, history, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    responses = [get(client, "/stability", hr, by_department="true"), get(client, "/forecast", hr),
                 get(client, f"/departments/{history['Engineering'].id}", hr)]
    forbidden = {"employee_id", "employee_code", "full_name", "work_email", "risk_score", "salary", "net_pay",
                 "manager_private_notes", "overall_rating", "user_id"}
    for body in responses:
        assert _keys(body) & forbidden == set()
    text = " ".join(str(b) for b in responses)
    assert all(e.full_name not in text and e.employee_code not in text for e in org["eng"])


# --- Forecast freshness and gaps ---------------------------------------------------------------------------------------


def test_forecast_refuses_to_project_from_stale_snapshots(client, db, org, headers_for, fixed_today) -> None:
    d = departments(db)
    for month in range(1, 7):  # Jan-Jun 2026 only; "today" is 2026-10-15
        add_snapshot(db, month_end(2026, month), {d["Engineering"]: (20 + month, 0, 0)})
    hr = headers_for(HR)
    stale = get(client, "/forecast", hr)["data"]
    assert (stale["status"], stale["projections"], stale["projected_net_change"]) == ("stale_data", [], None)
    assert stale["latest_snapshot_date"] == "2026-06-30" and "older than 45 days" in stale["reason"]
    assert len(stale["observed"]) == 6  # observations are still shown, clearly as history

    add_snapshot(db, date(2026, 10, 10), {d["Engineering"]: (27, 0, 0)})
    fresh = get(client, "/forecast", hr)["data"]
    assert fresh["status"] == "ok" and fresh["reason"] is None
    assert fresh["projections"][0]["month"] == "2026-11"  # projections start after the latest observed month
    assert fresh["missing_months_in_data_window"] == 3  # Jul-Sep were not observed and are not filled


def test_forecast_reports_gap_in_history(client, history, headers_for, fixed_today) -> None:
    data = get(client, "/forecast", headers_for(HR))["data"]
    assert data["status"] == "ok" and data["missing_months_in_data_window"] == 1  # April 2026
    assert [o["month"] for o in data["observed"]].count("2026-04") == 0


def test_forecast_is_deterministic(client, history, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    assert get(client, "/forecast", hr, horizon_months=6) == get(client, "/forecast", hr, horizon_months=6)


# --- Trend rows cover whole months, never the future ------------------------------------------------------------------


def test_monthly_rows_cover_whole_months_up_to_today(client, db, org, staff, headers_for, fixed_today) -> None:
    hr = headers_for(HR)
    joined_early, _ = staff(department="Engineering", date_of_joining=date(2026, 3, 5))
    add_departure(db, org["eng"][1], local(2026, 3, 3, 12))
    staff(department="Engineering", date_of_joining=date(2026, 10, 25))  # future-dated joiner (today is 2026-10-15)

    march = get(client, "/trends", hr, date_from="2026-03-20", date_to="2026-03-25")["data"]["monthly"]
    assert [(m["month"], m["joiners"], m["recorded_departures"], m["month_complete"]) for m in march] == [
        ("2026-03", 1, 1, True)]  # the whole of March, although the range starts on the 20th

    autumn = get(client, "/trends", hr, date_from="2026-09-01", date_to="2026-10-31")["data"]["monthly"]
    october = autumn[-1]
    assert october["month"] == "2026-10" and october["joiners"] == 0 and october["month_complete"] is False
    retention = get(client, "/retention", hr, date_from="2026-10-01", date_to="2026-12-31")["data"]["observed"]
    assert retention["joiners"] == 0  # not observed yet
    assert joined_early.status == EmploymentStatus.ACTIVE


# --- Data quality --------------------------------------------------------------------------------------------------------


def test_unknown_departure_dates_counted_in_bounded_chunks(db, staff, monkeypatch) -> None:
    recorded, _ = staff(status=EmploymentStatus.INACTIVE)
    add_departure(db, recorded, local(2026, 5, 1, 12))
    for _ in range(3):
        staff(status=EmploymentStatus.INACTIVE)
    staff()  # active employees never count
    expected = service.inactive_without_recorded_departure(db)
    monkeypatch.setattr(service, "AUDIT_LOOKUP_CHUNK", 1)
    assert expected == service.inactive_without_recorded_departure(db) == 3


# --- Recommendations use the same indicator --------------------------------------------------------------------------


def _department_history(db, name: str, actives: list[int], ages: list[int]) -> Department:
    """Department-level snapshot rows `ages` days before the real today (recommendations use the real clock)."""
    department = Department(name=name)
    db.add(department)
    db.flush()
    today = utcnow().date()
    for active, age in zip(actives, ages, strict=True):
        day = today - timedelta(days=age)
        snapshot = db.scalar(select(WorkforceSnapshot).where(WorkforceSnapshot.snapshot_date == day))
        if snapshot is None:
            snapshot = WorkforceSnapshot(snapshot_date=day, captured_at=utcnow(), period_start=day.replace(day=1),
                                         total_employees=0, active_employees=0, on_leave_employees=0, inactive_employees=0,
                                         joiners_in_period=0, recorded_departures_in_period=0, source="synthetic_test",
                                         data_quality={})
            db.add(snapshot)
            db.flush()
        db.add(WorkforceSnapshotDepartment(snapshot_id=snapshot.id, department_id=department.id, department_name=name,
                                           total_employees=active, active_employees=active, on_leave_employees=0,
                                           inactive_employees=0, joiners_in_period=0, recorded_departures_in_period=0))
    db.commit()
    return department


def _evaluate(client, headers, department: Department, strategy: str) -> dict:
    response = client.post("/api/recommendations/evaluate", headers=headers, json={
        "department_id": department.id, "include_individual": False, "strategy": strategy})
    assert response.status_code == 200, response.text
    return response.json()


STABILITY_RULES = {"rules-v1": "AGG_LOW_STABILITY", "rules-v2": "V2_STABILITY_REVIEW"}


@pytest.mark.parametrize("strategy", ["rules-v1", "rules-v2"])
def test_small_department_gets_no_low_stability_recommendation(client, db, headers_for, strategy) -> None:
    hr = headers_for(HR)
    small = _department_history(db, "Small team", [4, 3, 2], [70, 40, 10])
    result = _evaluate(client, hr, small, strategy)
    rule = STABILITY_RULES[strategy]
    assert not db.scalars(select(Recommendation).where(Recommendation.rule_id == rule)).all()
    assert any(s["rule_id"] == rule and s["status"] == "INSUFFICIENT_DATA" for s in result["not_actionable"])


@pytest.mark.parametrize("strategy", ["rules-v1", "rules-v2"])
def test_large_department_with_the_same_decline_is_flagged_once(client, db, headers_for, strategy) -> None:
    hr = headers_for(HR)
    large = _department_history(db, "Large team", [40, 30, 20], [70, 40, 10])  # score 50 < 60
    first = _evaluate(client, hr, large, strategy)
    rule = STABILITY_RULES[strategy]
    created = db.scalars(select(Recommendation).where(Recommendation.rule_id == rule)).all()
    assert len(created) == 1 and created[0].department_id == large.id
    assert created[0].evidence["stability_score"] == 50.0
    assert set(created[0].evidence) & {"employee_id", "employee_ids", "full_name"} == set()
    second = _evaluate(client, hr, large, strategy)  # unchanged evidence: no duplicate
    db.expire_all()
    assert len(db.scalars(select(Recommendation).where(Recommendation.rule_id == rule)).all()) == 1
    assert first["created"] >= 1 and second["created"] == 0


def test_stale_department_history_creates_no_stability_recommendation(client, db, headers_for) -> None:
    hr = headers_for(HR)
    stale = _department_history(db, "Stale team", [40, 30, 20], [130, 100, 70])  # latest snapshot 70 days old
    result = _evaluate(client, hr, stale, "rules-v1")
    assert not db.scalars(select(Recommendation).where(Recommendation.rule_id == "AGG_LOW_STABILITY")).all()
    assert any(s["rule_id"] == "AGG_LOW_STABILITY" and s["status"] == "INSUFFICIENT_DATA" for s in result["not_actionable"])


def test_manager_cannot_evaluate_another_departments_stability(client, db, org, headers_for) -> None:
    other = _department_history(db, "Other team", [40, 30, 20], [70, 40, 10])
    response = client.post("/api/recommendations/evaluate", headers=org["manager_h"], json={
        "department_id": other.id, "include_individual": False})
    assert response.status_code == 404
    assert not db.scalars(select(Recommendation)).all()
