"""Query-count scaling across list, summary, analytics and export endpoints (Sub-prompt 28).

The same synthetic world is measured at two sizes in one in-memory SQLite database: every request is made once, then
each department grows by GROWTH employees (each with a login, leave request, attendance record, task, intervention,
goal, performance review, risk alert, payroll record and notifications), and every request is made again. A request
whose statement count grows with the data has an N+1 (or per-row) query pattern.

These are statement counts, which are deterministic; no wall-clock thresholds. They say nothing about MySQL timings.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Employee, SalaryComponentKind, UserRole
from app.models.base import utcnow
from tests.conftest import local
from tests.test_recommendations_api import make_alert  # noqa: F401  (pytest fixture)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
INITIAL, GROWTH = 2, 6

# (label, path, params, who) — page_size=100 so every row is on the page and serialized.
P = {"page_size": 100}
REQUESTS = [
    ("employees", "/api/employees", P, "hr"),
    ("employees (manager)", "/api/employees", P, "manager"),
    ("department members", "/api/departments/{eng}/employees", P, "hr"),
    ("departments", "/api/departments", P, "hr"),
    ("attendance", "/api/attendance", {**P, "date_from": "2026-03-01", "date_to": "2026-03-31"}, "hr"),
    ("attendance summary", "/api/attendance/summary", {"date_from": "2026-03-01", "date_to": "2026-03-31"}, "hr"),
    ("leave review queue", "/api/leave/requests", {**P, "status": "PENDING"}, "hr"),
    ("leave review queue (manager)", "/api/leave/requests", {**P, "status": "PENDING"}, "manager"),
    ("payroll records", "/api/payroll/periods/{period}/records", P, "hr"),
    ("payroll profiles", "/api/payroll/profiles", P, "hr"),
    ("tasks", "/api/tasks", P, "hr"),
    ("tasks (manager)", "/api/tasks", P, "manager"),
    ("task summary", "/api/tasks/summary", {}, "hr"),
    ("interventions", "/api/interventions", P, "hr"),
    ("intervention summary", "/api/interventions/summary", {}, "hr"),
    ("recommendations", "/api/recommendations", {**P, "status": "AVAILABLE"}, "hr"),
    ("recommendation summary", "/api/recommendations/summary", {}, "hr"),
    ("goals", "/api/performance/goals", P, "hr"),
    ("reviews", "/api/performance/reviews", P, "hr"),
    ("performance summary", "/api/performance/summary", {}, "hr"),
    ("risk alerts", "/api/risk-monitoring/alerts", P, "hr"),
    ("risk summary", "/api/risk-monitoring/summary", {}, "hr"),
    ("notifications", "/api/notifications/me", P, "manager"),
    ("audit logs", "/api/audit-logs", P, "hr"),
    ("audit logs (manager)", "/api/audit-logs", P, "manager"),
    ("activity feed", "/api/workforce-activity/feed", P, "hr"),
    ("activity summary", "/api/workforce-activity/summary", {}, "hr"),
    ("global search", "/api/search", {"q": "Employee", "limit_per_type": 20}, "hr"),
    ("analytics overview", "/api/workforce-analytics/overview", {}, "hr"),
    ("analytics retention", "/api/workforce-analytics/retention", {}, "hr"),
    ("stability by department", "/api/workforce-analytics/stability", {"by_department": "true"}, "hr"),
    ("attrition summary", "/api/ai/attrition/summary", {}, "hr"),
    ("report: employees", "/api/reports/employees", {"format": "csv"}, "hr"),
    ("report: attendance", "/api/reports/attendance", {"format": "csv", "date_from": "2026-03-01", "date_to": "2026-03-31"}, "hr"),
    ("report: leave", "/api/reports/leave", {"format": "csv", "date_from": "2026-03-01", "date_to": "2026-03-31"}, "hr"),
    ("report: payroll", "/api/reports/payroll", {"format": "csv", "period_id": "{period}"}, "hr"),
    ("report: activity", "/api/reports/activity", {"format": "csv"}, "hr"),
    ("audit export", "/api/audit-logs/export", {"format": "csv"}, "hr"),
]


@pytest.fixture
def world(client, db, staff, headers_for, make_leave_type, make_policy, make_component, make_profile, make_alert,
          leave_clock, clock):
    w = {"hr": headers_for(HR), "n": 0}
    w["manager_emp"], w["manager"] = staff(MANAGER, department="Engineering")
    staff(MANAGER, department="Sales")
    w["eng"] = w["manager_emp"].department_id
    leave_type = make_leave_type("ANNUAL", "Annual Leave")
    make_policy(leave_type, annual_allowance_days=20)
    base = make_component("BASIC_ALLOWANCE", SalaryComponentKind.EARNING)
    cycle = client.post("/api/performance/cycles", headers=w["hr"], json={
        "name": "Scaling cycle", "period_start": (date.today() - timedelta(days=60)).isoformat(),
        "period_end": (date.today() + timedelta(days=30)).isoformat(),
        "self_assessment_deadline": (date.today() + timedelta(days=10)).isoformat(),
        "manager_review_deadline": (date.today() + timedelta(days=20)).isoformat()}).json()["id"]
    client.post(f"/api/performance/cycles/{cycle}/transition", headers=w["hr"], json={"action": "open"})
    reviewers = {d: db.scalar(select(Employee.user_id).where(Employee.department_id == d, Employee.user_id.is_not(None)))
                 for d in {w["eng"], db.scalar(select(Employee.department_id).where(Employee.department_id != w["eng"]))}}

    def grow(count: int, month: int) -> None:
        for department in ("Engineering", "Sales"):
            for _ in range(count):
                w["n"] += 1
                employee, headers = staff(department=department, full_name=f"Employee Scale {w['n']:03d}")
                assert client.post("/api/leave/requests", headers=headers, json={
                    "leave_type_id": leave_type.id, "start_date": "2026-03-09", "end_date": "2026-03-10",
                    "reason": "Family commitments"}).status_code == 201
                assert client.post("/api/attendance/check-in", headers=headers, json={}).status_code == 201
                assert client.post("/api/tasks", headers=w["hr"], json={
                    "title": f"Follow-up {w['n']}", "assignee_employee_id": employee.id}).status_code == 201
                assert client.post("/api/interventions", headers=w["hr"], json={
                    "employee_id": employee.id, "category": "MANAGER_CHECK_IN", "title": f"Check-in {w['n']}"}).status_code == 201
                assert client.post("/api/performance/goals", headers=w["hr"], json={
                    "employee_id": employee.id, "title": f"Goal {w['n']}", "measurable_target": "Ship the milestone",
                    "start_date": date.today().isoformat(), "target_date": (date.today() + timedelta(days=30)).isoformat()}).status_code == 201
                assert client.post("/api/performance/reviews", headers=w["hr"], json={
                    "cycle_id": cycle, "employee_id": employee.id,
                    "reviewer_user_id": reviewers[employee.department_id]}).status_code == 201
                make_alert(employee)
                make_profile(employee, "40000.00", components=((base, "1000.00"),))
        assert client.post("/api/workforce-analytics/snapshots/capture", headers=w["hr"], json={}).status_code in (201, 409)
        assert client.post("/api/recommendations/evaluate", headers=w["hr"], json={}).status_code == 200
        period = client.post("/api/payroll/periods", headers=w["hr"], json={"year": 2026, "month": month}).json()["id"]
        assert client.post(f"/api/payroll/periods/{period}/calculate", headers=w["hr"], json={}).status_code == 200
        w["period"] = period

    w["grow"] = grow
    clock.set(local(2026, 3, 10, 9, 0))
    return w


def _measure(client, count_queries, w) -> dict[str, int]:
    counts = {}
    for label, path, params, who in REQUESTS:
        url = path.format(eng=w["eng"], period=w["period"])
        query = {k: (str(v).format(period=w["period"]) if isinstance(v, str) else v) for k, v in params.items()}
        before = len(count_queries())
        response = client.get(url, headers=w[who], params=query)
        assert response.status_code == 200, (label, response.text)
        counts[label] = len(count_queries()) - before
    return counts


def test_statement_counts_do_not_grow_with_the_data(client, db, world, count_queries) -> None:
    world["grow"](INITIAL, 3)
    small = _measure(client, count_queries, world)
    world["grow"](GROWTH, 4)
    large = _measure(client, count_queries, world)
    report = {label: (small[label], large[label]) for label in small}
    print("\nstatements per request (small world -> grown world):")
    for label, (a, b) in report.items():
        print(f"  {label:32s} {a:4d} -> {b:4d}{'   GROWS' if b > a else ''}")
    growing = {label: counts for label, counts in report.items() if counts[1] > counts[0]}
    assert growing == {}


# --- Write paths: SELECT statements must not grow per employee (inserts naturally do) ---------------------------------


def _selects(count_queries, start: int) -> int:
    return sum(1 for s in count_queries()[start:] if s.lstrip().upper().startswith("SELECT"))


def test_payroll_calculation_reads_inputs_in_grouped_queries(client, db, staff, headers_for, make_component, make_profile,
                                                             count_queries) -> None:
    hr = headers_for(HR)
    base = make_component("BASIC_ALLOWANCE", SalaryComponentKind.EARNING)

    def calculate(month: int) -> int:
        period = client.post("/api/payroll/periods", headers=hr, json={"year": 2026, "month": month}).json()["id"]
        before = len(count_queries())
        response = client.post(f"/api/payroll/periods/{period}/calculate", headers=hr, json={})
        assert response.status_code == 200 and response.json()["skipped"] == []
        return _selects(count_queries, before)

    def add(count: int) -> None:
        for i in range(count):
            employee, _ = staff(department=("Engineering", "Sales")[i % 2])
            make_profile(employee, "40000.00", components=((base, "1000.00"),))

    add(2)
    small = calculate(3)
    add(10)
    large = calculate(4)
    assert large == small, (small, large)


def test_monitoring_run_reads_reference_data_once(client, db, staff, headers_for, count_queries) -> None:
    from app.models import AttritionModelVersion, ModelVersionStatus
    from ml.attrition.features import FEATURE_SCHEMA_VERSION
    from tests.test_risk_monitoring_api import add_prediction

    hr = headers_for(HR)
    headers_for(HR, "second.hr@example.com")
    model = AttritionModelVersion(model_version="lr-20260101000000-0000000a", feature_schema_version=FEATURE_SCHEMA_VERSION,
                                  status=ModelVersionStatus.ACTIVE, trained_at=utcnow(), dataset_fingerprint="0" * 64,
                                  artifact_sha256="0" * 64, training_rows=100, evaluation={}, activated_at=utcnow())
    db.add(model)
    db.commit()

    def crossing(count: int) -> int:
        for _ in range(count):
            employee, _ = staff(department="Engineering")
            add_prediction(db, employee, model, 30, days_ago=5)
            add_prediction(db, employee, model, 85, days_ago=1)  # crosses the HIGH threshold: new alert, HR notified
        before = len(count_queries())
        response = client.post("/api/risk-monitoring/run", headers=hr, json={})
        assert response.status_code == 200 and response.json()["alerts_created"] == count, response.text
        return _selects(count_queries, before)

    small = crossing(2)
    large = crossing(8)
    assert large == small, (small, large)
