"""Frontend/backend query contract (Sub-prompt 29 QA).

The backend rejects unknown query parameters (extra="forbid"), so a filter the React app sends that the API does not
accept would silently break a page with a 422. This test replays every GET request the frontend makes (paths, filter
names and every option value offered in the UI, as listed in frontend/src/pages/*.jsx) as the role that would send it,
and asserts the backend accepts it. Booleans are sent as "true" only, because the app's cleanParams() drops false.
"""

from datetime import date, timedelta

import pytest

from app.models import UserRole
from tests.conftest import local

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
TODAY = date.today()
RANGE = {"date_from": (TODAY - timedelta(days=29)).isoformat(), "date_to": TODAY.isoformat()}

# (who, path, fixed params, {filter: [every option value the UI offers]})
REQUESTS = [
    # Activity.jsx
    ("hr", "/workforce-activity/feed", {"page": 1, "page_size": 25, **RANGE, "department_id": "{eng}"}, {
        "category": ["attendance", "leave", "employee_record", "employee_status", "risk_event", "risk_alert",
                     "risk_escalation", "intervention", "recommendation"],
        "kind": ["OBSERVED_EVENT", "RISK_EVENT"], "priority": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]}),
    # Analytics.jsx
    ("hr", "/workforce-analytics/overview", {"department_id": "{eng}"}, {}),
    ("hr", "/workforce-analytics/trends", {"department_id": "{eng}"}, {}),
    ("hr", "/workforce-analytics/forecast", {"department_id": "{eng}", "horizon_months": 6}, {}),
    ("hr", "/workforce-analytics/stability", {"by_department": "true"}, {}),
    ("hr", "/workforce-analytics/stability", {"department_id": "{eng}"}, {}),
    ("manager", "/workforce-analytics/stability", {"by_department": "true"}, {}),
    # Attendance.jsx
    ("employee", "/attendance/me", {**RANGE, "page": 1, "page_size": 20}, {}),
    ("employee", "/attendance/me/summary", RANGE, {}),
    ("employee", "/shifts", {"page_size": 100}, {}),
    ("hr", "/attendance", {**RANGE, "department_id": "{eng}", "page": 1, "page_size": 20},
     {"status": ["PRESENT", "LATE", "HALF_DAY", "ABSENT", "ON_LEAVE"]}),
    ("hr", "/attendance/summary", {**RANGE, "department_id": "{eng}"}, {"status": ["PRESENT", "ABSENT"]}),
    ("manager", "/attendance/summary", RANGE, {}),
    # Audit.jsx (category/action/entity_type values come from the catalog; see the dedicated test)
    ("hr", "/audit-logs/catalog", {}, {}),
    ("hr", "/audit-logs", {**RANGE, "page": 1, "page_size": 25}, {"sort": ["newest", "oldest"]}),
    ("hr", "/audit-logs/export", RANGE, {"format": ["csv", "xlsx", "pdf"], "sort": ["newest", "oldest"]}),
    # Dashboard.jsx
    ("hr", "/workforce-analytics/overview", {}, {}),
    ("employee", "/departments", {"page_size": 100}, {}),
    ("hr", "/risk-monitoring/summary", {}, {}),
    ("hr", "/payroll/periods", {"page_size": 1}, {}),
    ("hr", "/workforce-activity/feed", {"page_size": 6}, {}),
    ("employee", "/tasks", {"status": "OPEN", "sort": "priority", "page_size": 5}, {}),
    ("employee", "/employees", {"status": "ACTIVE", "page_size": 1}, {}),
    ("employee", "/departments", {"is_active": "true", "page_size": 1}, {}),
    ("manager", "/leave/requests", {"status": "PENDING", "page_size": 1}, {}),
    ("employee", "/leave/balances/me", {}, {}),
    ("employee", "/tasks/summary", {}, {}),
    # Datasets.jsx
    ("admin", "/datasets", {"q": "hr", "include_archived": "true", "page": 1, "page_size": 20},
     {"status": ["UPLOADED", "VALIDATING", "VALIDATED", "REJECTED", "ARCHIVED"]}),
    # Departments.jsx
    ("employee", "/departments", {"q": "Eng", "page": 1, "page_size": 20}, {"is_active": ["true", "false"]}),
    # Employees.jsx
    ("hr", "/employees", {"q": "Emp", "department_id": "{eng}", "page": 1, "page_size": 20}, {
        "status": ["ACTIVE", "ON_LEAVE", "INACTIVE"], "sort_by": ["full_name", "employee_code", "date_of_joining", "created_at"],
        "sort_order": ["asc", "desc"]}),
    # Interventions.jsx
    ("hr", "/interventions", {"q": "Check", "assigned_to_me": "true", "page": 1, "page_size": 20}, {
        "status": ["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"], "priority": ["LOW", "MEDIUM", "HIGH"],
        "overdue": ["true", "false"]}),
    ("hr", "/interventions/summary", {}, {}),
    # Leave.jsx
    ("employee", "/leave/types", {"page_size": 100}, {}),
    ("employee", "/leave/requests/me", {"page": 1, "page_size": 20}, {"status": ["PENDING", "APPROVED", "REJECTED", "CANCELLED"]}),
    ("hr", "/leave/requests", {**RANGE, "department_id": "{eng}", "page": 1, "page_size": 20},
     {"status": ["PENDING", "APPROVED", "REJECTED", "CANCELLED"]}),
    # Notifications.jsx
    ("employee", "/notifications/me", {"unread_only": "true", "page": 1, "page_size": 20}, {"sort": ["priority", "recent"]}),
    # Payroll.jsx
    ("hr", "/payroll/periods/{period}/records", {"department_id": "{eng}", "page": 1, "page_size": 20},
     {"status": ["CALCULATED", "APPROVED", "PAID"]}),
    ("hr", "/payroll/periods", {"year": TODAY.year, "page_size": 24}, {}),
    ("hr", "/reports/payroll", {"period_id": "{period}", "department_id": "{eng}"},
     {"format": ["csv", "xlsx", "pdf"], "status": ["CALCULATED", "APPROVED", "PAID"]}),
    ("employee", "/payroll/me", {"page": 1, "page_size": 12}, {}),
    # Performance.jsx
    ("employee", "/performance/goals", {"q": "Goal", "mine": "true", "page": 1, "page_size": 20},
     {"status": ["NOT_STARTED", "IN_PROGRESS", "AT_RISK", "COMPLETED", "CANCELLED"]}),
    ("manager", "/performance/reviews", {"page": 1, "page_size": 20},
     {"status": ["ASSIGNED", "SELF_SUBMITTED", "MANAGER_SUBMITTED", "FINALIZED", "CANCELLED"],
      "mine": ["true"], "to_review": ["true"]}),
    ("employee", "/performance/cycles", {"page": 1, "page_size": 20}, {}),
    # Recommendations.jsx
    ("hr", "/recommendations", {"page": 1, "page_size": 20}, {
        "status": ["AVAILABLE", "ACCEPTED", "DISMISSED", "COMPLETED", "SUPERSEDED"],
        "priority": ["LOW", "MEDIUM", "HIGH", "CRITICAL"], "rule_version": ["rules-v1", "rules-v2"],
        "sort": ["rank", "evaluated"]}),
    ("hr", "/recommendations/summary", {}, {}),
    # Reports.jsx (per-report parameters are built from the catalog; see the dedicated test)
    ("manager", "/reports/catalog", {}, {}),
    # Risk.jsx
    ("hr", "/risk-monitoring/alerts", {"department_id": "{eng}", "detected_from": RANGE["date_from"],
                                       "detected_to": RANGE["date_to"], "page": 1, "page_size": 20},
     {"status": ["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"], "priority": ["LOW", "MEDIUM", "HIGH"]}),
    # Search.jsx (types are repeated keys, as axios sends them with indexes: null)
    ("employee", "/search", {"q": "Employee", "types": ["employee", "task", "goal"]}, {"limit_per_type": ["5", "10", "20"]}),
    # Tasks.jsx
    ("manager", "/employees", {"status": "ACTIVE", "q": "Emp", "page_size": 50}, {}),
    ("employee", "/tasks", {"q": "Follow", "assigned_to_me": "true", "page": 1, "page_size": 20}, {
        "status": ["OPEN", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED"], "priority": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        "overdue": ["true", "false"], "sort": ["updated", "due", "priority", "created"]}),
]


@pytest.fixture
def people(client, staff, headers_for):
    hr_employee, hr = staff(HR, department="People")
    manager, manager_h = staff(MANAGER, department="Engineering")
    _, employee_h = staff(EMPLOYEE, department="Engineering")
    period = client.post("/api/payroll/periods", headers=hr, json={"year": TODAY.year, "month": TODAY.month}).json()["id"]
    return {"hr": hr, "manager": manager_h, "employee": employee_h, "admin": headers_for(ADMIN),
            "eng": manager.department_id, "period": period}


def _fill(value, people):
    if isinstance(value, str):
        return value.replace("{eng}", str(people["eng"])).replace("{period}", str(people["period"]))
    return value


def _cases():
    for who, path, fixed, options in REQUESTS:
        if not options:
            yield who, path, fixed
        for name, values in options.items():
            for value in values:
                yield who, path, {**fixed, name: value}


CASES = list(_cases())


@pytest.mark.parametrize(("who", "path", "params"), CASES, ids=[f"{w}:{p}:{sorted(q.items())}" for w, p, q in CASES])
def test_every_frontend_query_is_accepted(client, people, who, path, params) -> None:
    response = client.get(f"/api{_fill(path, people)}", headers=people[who],
                          params={k: _fill(v, people) for k, v in params.items()})
    assert response.status_code == 200, response.text


def test_audit_filters_from_the_catalog_are_accepted(client, people) -> None:
    catalog = client.get("/api/audit-logs/catalog", headers=people["hr"]).json()
    filters = []
    for key in ("categories", "actions", "entity_types"):
        assert catalog.get(key), f"catalog lacks {key}"
    for category in catalog["categories"]:
        filters.append({"category": category["id"] if isinstance(category, dict) else category})
    for action in catalog["actions"][:20]:
        filters.append({"action": action["action"] if isinstance(action, dict) else action})
    for entity_type in catalog["entity_types"]:
        filters.append({"entity_type": entity_type})
    for extra in filters:
        response = client.get("/api/audit-logs", headers=people["hr"], params={**RANGE, "page_size": 25, **extra})
        assert response.status_code == 200, (extra, response.text)


def test_reports_built_from_the_catalog_are_accepted(client, people) -> None:
    """Reports.jsx: format, period_id when listed, department_id for HR/ADMIN only, date_from/date_to when listed."""
    for who, can_pick in (("hr", True), ("manager", False)):
        catalog = client.get("/api/reports/catalog", headers=people[who]).json()
        assert catalog["reports"]
        for report in catalog["reports"]:
            for fmt in report["formats"]:
                params = {"format": fmt}
                if "period_id" in report["filters"]:
                    params["period_id"] = people["period"]
                if "department_id" in report["filters"] and can_pick:
                    params["department_id"] = people["eng"]
                if "date_from" in report["filters"]:
                    params.update(RANGE)
                path = report["path"]
                response = client.get(path, headers=people[who], params=params)
                assert response.status_code == 200, (who, path, params, response.text)
                assert response.headers["content-disposition"].startswith("attachment;")


def test_activity_updates_with_frontend_filters(client, people, clock) -> None:
    """Activity.jsx: the manual "check for new activity" call sends the cursor plus the current filters."""
    clock.set(local(TODAY.year, TODAY.month, TODAY.day, 9, 0))
    feed = client.get("/api/workforce-activity/feed", headers=people["hr"],
                      params={"category": "leave", "priority": "HIGH", "department_id": people["eng"]}).json()
    cursor = feed["updates_cursor"]
    response = client.get("/api/workforce-activity/updates", headers=people["hr"], params={
        "cursor": cursor, "category": "leave", "priority": "HIGH", "department_id": people["eng"]})
    assert response.status_code == 200, response.text
