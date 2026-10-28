"""Authorization matrix across every registered route, plus cross-module object-level and escalation scenarios.

Synthetic records in in-memory SQLite only (no real database, no real credentials).

Layer 1, roles: every route's role requirement is compared with the reviewed matrix below. Every protected route rejects
anonymous and invalid tokens with 401, and every role outside the matrix with 403, before any handler code runs.
Layer 2, scope and objects: managers stay inside their own (current) department, employees inside their own records,
linked workflows (tasks, interventions, goals, reviews, leave) cannot be used to reach out-of-scope records, and
protected fields cannot be injected. See docs/SECURITY_RBAC.md.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select

from app.api import (
    attendance,
    attrition,
    audit_logs,
    auth,
    datasets,
    departments,
    employees,
    health,
    interventions,
    leave,
    notifications,
    payroll,
    performance,
    recommendations,
    reports,
    risk_monitoring,
    search,
    shifts,
    tasks,
    users,
    workforce_activity,
    workforce_analytics,
)
from app.api.deps import get_current_user
from app.core import security
from app.core.config import get_settings
from app.main import app
from app.models import AuditLog, Notification, User, UserRole
from tests.conftest import ORG_TZ, TEST_PASSWORD, auth_header

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
ROLE_DENIED = "You do not have permission to perform this action"
ROUTERS = (health, auth, departments, employees, shifts, attendance, leave, notifications, payroll, attrition, datasets,
           risk_monitoring, workforce_analytics, interventions, recommendations, workforce_activity, reports, audit_logs,
           tasks, performance, search, users)

# --- The reviewed route matrix (paths without the /api prefix) -------------------------------------------------------
# PUBLIC: no authentication. AUTHENTICATED: any signed-in user, no role dependency (the handler scopes the data).
# ALL / STAFF / HR_ADMIN / ADMIN_ONLY: require_roles with all four roles / ADMIN+HR+MANAGER / ADMIN+HR / ADMIN.
# "ALL" routes still enforce record-level rules inside the handler (see the scenario tests below).
ROLE_GROUPS = {
    "PUBLIC": None,
    "AUTHENTICATED": None,
    "ALL": frozenset({ADMIN, HR, MANAGER, EMPLOYEE}),
    "STAFF": frozenset({ADMIN, HR, MANAGER}),
    "HR_ADMIN": frozenset({ADMIN, HR}),
    "ADMIN_ONLY": frozenset({ADMIN}),
}
EXPECTED = {
    "PUBLIC": [
        "GET /health/db",
        "POST /auth/register",
        "POST /auth/login",
    ],
    "AUTHENTICATED": [
        "GET /auth/me",
        "POST /auth/logout",
        "GET /reports/catalog",
        "GET /search",
    ],
    "ALL": [
        "GET /departments",
        "GET /departments/{department_id}",
        "GET /employees",
        "GET /employees/search",
        "GET /employees/{employee_id}",
        "GET /shifts",
        "GET /shifts/{shift_id}",
        "POST /attendance/check-in",
        "POST /attendance/check-out",
        "GET /attendance/me",
        "GET /attendance/me/summary",
        "GET /attendance/{attendance_id}",
        "GET /leave/types",
        "GET /leave/policies",
        "POST /leave/requests",
        "GET /leave/requests/me",
        "GET /leave/balances/me",
        "GET /leave/requests/{request_id}",
        "POST /leave/requests/{request_id}/cancel",
        "GET /notifications/me",
        "POST /notifications/{notification_id}/read",
        "GET /payroll/me",
        "GET /payroll/me/{record_id}/payslip",
        "GET /tasks/summary",
        "GET /tasks",
        "GET /tasks/{task_id}",
        "PATCH /tasks/{task_id}",
        "POST /tasks/{task_id}/assign",
        "POST /tasks/{task_id}/transition",
        "GET /tasks/{task_id}/history",
        "GET /performance/summary",
        "GET /performance/cycles",
        "GET /performance/cycles/{cycle_id}",
        "PATCH /performance/cycles/{cycle_id}",
        "POST /performance/cycles/{cycle_id}/transition",
        "POST /performance/goals",
        "GET /performance/goals",
        "GET /performance/goals/{goal_id}",
        "GET /performance/goals/{goal_id}/history",
        "PATCH /performance/goals/{goal_id}",
        "POST /performance/reviews",
        "GET /performance/reviews",
        "GET /performance/reviews/{review_id}",
        "GET /performance/reviews/{review_id}/history",
        "PATCH /performance/reviews/{review_id}",
        "POST /performance/reviews/{review_id}/submit-self-assessment",
        "POST /performance/reviews/{review_id}/submit-manager-review",
        "POST /performance/reviews/{review_id}/finalize",
        "POST /performance/reviews/{review_id}/reopen",
        "POST /performance/reviews/{review_id}/cancel",
    ],
    "STAFF": [
        "GET /departments/{department_id}/employees",
        "GET /attendance",
        "GET /attendance/summary",
        "GET /leave/requests",
        "POST /leave/requests/{request_id}/approve",
        "POST /leave/requests/{request_id}/reject",
        "GET /leave/conflicts",
        "POST /ai/attrition/predict/{employee_id}",
        "POST /ai/attrition/predict-batch",
        "GET /ai/attrition/history/{employee_id}",
        "GET /risk-monitoring/alerts",
        "GET /risk-monitoring/alerts/{alert_id}",
        "POST /risk-monitoring/alerts/{alert_id}/acknowledge",
        "POST /risk-monitoring/alerts/{alert_id}/resolve",
        "GET /risk-monitoring/employees/{employee_id}/history",
        "GET /risk-monitoring/summary",
        "POST /risk-monitoring/escalations/evaluate",
        "GET /workforce-analytics/overview",
        "GET /workforce-analytics/trends",
        "GET /workforce-analytics/forecast",
        "GET /workforce-analytics/stability",
        "GET /workforce-analytics/retention",
        "GET /workforce-analytics/departments/{department_id}",
        "GET /interventions/categories",
        "GET /interventions/summary",
        "POST /interventions",
        "GET /interventions",
        "GET /interventions/{intervention_id}",
        "PATCH /interventions/{intervention_id}",
        "POST /interventions/{intervention_id}/assign",
        "POST /interventions/{intervention_id}/start",
        "POST /interventions/{intervention_id}/complete",
        "POST /interventions/{intervention_id}/cancel",
        "POST /interventions/{intervention_id}/notes",
        "GET /interventions/{intervention_id}/history",
        "GET /recommendations/summary",
        "POST /recommendations/evaluate",
        "GET /recommendations",
        "GET /recommendations/{recommendation_id}",
        "POST /recommendations/{recommendation_id}/accept",
        "POST /recommendations/{recommendation_id}/dismiss",
        "POST /recommendations/{recommendation_id}/complete",
        "POST /recommendations/{recommendation_id}/create-intervention",
        "GET /recommendations/{recommendation_id}/history",
        "POST /recommendations/{recommendation_id}/link-task",
        "GET /workforce-activity/sources",
        "GET /workforce-activity/feed",
        "GET /workforce-activity/summary",
        "GET /workforce-activity/updates",
        "GET /workforce-activity/status",
        "GET /workforce-activity/signals",
        "GET /workforce-activity/departments/{department_id}",
        "GET /reports/employees",
        "GET /reports/attendance",
        "GET /reports/leave",
        "GET /reports/attrition",
        "GET /reports/workforce",
        "GET /reports/activity",
        "GET /audit-logs/catalog",
        "GET /audit-logs",
        "POST /tasks",
    ],
    "HR_ADMIN": [
        "POST /departments",
        "PATCH /departments/{department_id}",
        "POST /employees",
        "PATCH /employees/{employee_id}",
        "PATCH /employees/{employee_id}/status",
        "POST /shifts",
        "PATCH /shifts/{shift_id}",
        "PATCH /shifts/{shift_id}/status",
        "PATCH /attendance/{attendance_id}/correction",
        "POST /leave/types",
        "PATCH /leave/types/{leave_type_id}",
        "POST /leave/policies",
        "PATCH /leave/policies/{policy_id}",
        "POST /payroll/components",
        "GET /payroll/components",
        "PATCH /payroll/components/{component_id}",
        "POST /payroll/profiles",
        "GET /payroll/profiles",
        "GET /payroll/profiles/{profile_id}",
        "PATCH /payroll/profiles/{profile_id}",
        "POST /payroll/periods",
        "GET /payroll/periods",
        "GET /payroll/periods/{period_id}",
        "POST /payroll/periods/{period_id}/calculate",
        "GET /payroll/periods/{period_id}/records",
        "GET /payroll/records/{record_id}",
        "POST /payroll/records/{record_id}/approve",
        "POST /payroll/records/{record_id}/mark-paid",
        "GET /ai/attrition/summary",
        "GET /ai/attrition/model-status",
        "GET /datasets",
        "GET /datasets/{dataset_id}",
        "GET /datasets/{dataset_id}/versions",
        "GET /datasets/{dataset_id}/validation",
        "POST /risk-monitoring/run",
        "POST /risk-monitoring/alerts/{alert_id}/assign",
        "POST /workforce-analytics/snapshots/capture",
        "GET /workforce-analytics/snapshots",
        "GET /reports/payroll",
        "GET /audit-logs/export",
        "POST /performance/cycles",
    ],
    "ADMIN_ONLY": [
        "POST /ai/attrition/train",
        "POST /ai/attrition/models/{model_version}/activate",
        "POST /datasets",
        "POST /datasets/{dataset_id}/versions",
        "POST /datasets/{dataset_id}/validate",
        "POST /datasets/{dataset_id}/archive",
        "GET /datasets/{dataset_id}/download",
        # Login-account administration (Sub-prompt 31)
        "GET /users",
        "GET /users/{user_id}",
        "PATCH /users/{user_id}/status",
        "PATCH /users/{user_id}/role",
        "POST /users/{user_id}/revoke-sessions",
    ],
}
EXPECTED["PUBLIC"].append("GET /health")  # declared in app.main


def _expected_routes() -> dict[str, str]:
    routes = {}
    for group, items in EXPECTED.items():
        for item in items:
            assert item not in routes, f"{item} listed twice"
            routes[item] = group
    return routes


EXPECTED_ROUTES = _expected_routes()
PROTECTED = sorted(k for k, g in EXPECTED_ROUTES.items() if g != "PUBLIC")
ROLE_LIMITED = sorted(k for k, g in EXPECTED_ROUTES.items() if ROLE_GROUPS[g] is not None and len(ROLE_GROUPS[g]) < 4)


def _route_dependencies(route: APIRoute) -> tuple[frozenset | None, bool]:
    """(roles required by require_roles or None, whether get_current_user runs)."""
    roles, authenticated = None, False
    stack = [route.dependant]
    while stack:
        dep = stack.pop()
        allowed = getattr(dep.call, "allowed_roles", None)
        if allowed is not None:
            assert roles is None, f"{route.path}: more than one role dependency"
            roles = frozenset(allowed)
        authenticated |= dep.call is get_current_user
        stack.extend(dep.dependencies)
    return roles, authenticated


def _actual_routes() -> dict[str, tuple[frozenset | None, bool]]:
    actual = {}
    for module in ROUTERS:
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    actual[f"{method} {route.path.removeprefix('/api')}"] = _route_dependencies(route)
    return actual


def _url(route_key: str) -> tuple[str, str]:
    method, path = route_key.split(" ", 1)
    path = path.replace("{model_version}", "v-test")
    while "{" in path:
        start = path.index("{")
        path = path[:start] + "999" + path[path.index("}", start) + 1:]
    return method, f"/api{path}"


# --- Layer 1: the route table ------------------------------------------------------------------------------------------


def test_every_registered_route_is_in_the_reviewed_matrix() -> None:
    """A new or changed route fails here until its permissions are reviewed and added to the matrix."""
    schema_routes = {f"{m.upper()} {p.removeprefix('/api')}" for p, ops in app.openapi()["paths"].items() for m in ops}
    actual = _actual_routes()
    assert set(actual) | {"GET /health"} == schema_routes, "a router is registered that this test does not inspect"
    assert set(EXPECTED_ROUTES) == schema_routes


def test_route_role_requirements_match_the_matrix() -> None:
    mismatches = []
    for key, (roles, authenticated) in _actual_routes().items():
        group = EXPECTED_ROUTES[key]
        if group == "PUBLIC":
            ok = roles is None and not authenticated
        elif group == "AUTHENTICATED":
            ok = roles is None and authenticated
        else:
            ok = roles == ROLE_GROUPS[group] and authenticated
        if not ok:
            mismatches.append((key, group, sorted(r.value for r in roles) if roles else roles, authenticated))
    assert mismatches == []


def test_only_health_register_and_login_are_public() -> None:
    assert sorted(EXPECTED["PUBLIC"]) == ["GET /health", "GET /health/db", "POST /auth/login", "POST /auth/register"]


def test_only_administrators_can_assign_roles_or_administer_accounts() -> None:
    """Since Sub-prompt 31 roles can be assigned through the API, but only by PATCH /api/users/{user_id}/role, and every
    account-administration route is ADMIN only. No other request schema accepts a role (registration included)."""
    accepting_role = []
    for module in ROUTERS:
        for route in module.router.routes:
            if not isinstance(route, APIRoute) or route.body_field is None:
                continue
            body = route.body_field.field_info.annotation
            if isinstance(body, type) and issubclass(body, BaseModel) and "role" in body.model_fields:
                accepting_role.append(f"{sorted(route.methods)[0]} {route.path}")
    assert accepting_role == ["PATCH /api/users/{user_id}/role"]
    user_routes = [k for k in EXPECTED_ROUTES if k.split(" ", 1)[1].startswith("/users")]
    assert len(user_routes) == 5 and all(EXPECTED_ROUTES[k] == "ADMIN_ONLY" for k in user_routes)


@pytest.mark.parametrize("route_key", PROTECTED)
def test_protected_routes_reject_missing_and_invalid_tokens(client, make_user, route_key) -> None:
    method, url = _url(route_key)
    inactive = make_user(email="inactive@example.com", role=ADMIN, is_active=False)
    settings = get_settings()
    expired = jwt.encode({"sub": "1", "iss": settings.jwt_issuer, "iat": datetime(2020, 1, 1, tzinfo=UTC),
                          "exp": datetime(2020, 1, 1, 0, 5, tzinfo=UTC), "jti": "x", "type": "access"},
                         settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)
    cases = {
        "missing": None,
        "malformed": "not-a-jwt",
        "expired": expired,
        "wrong_key": jwt.encode({"sub": str(inactive.id), "iss": settings.jwt_issuer, "iat": datetime.now(UTC),
                                 "exp": datetime.now(UTC) + timedelta(minutes=5), "jti": "x", "type": "access"},
                                "a-different-secret-key-that-is-long-enough-000", algorithm=settings.jwt_algorithm),
        "unknown_user": security.create_access_token(987654).token,
        "inactive_user": security.create_access_token(inactive.id).token,
    }
    for name, token in cases.items():
        headers = auth_header(token) if token else {}
        response = client.request(method, url, headers=headers)
        assert response.status_code == 401, (name, response.text)
        assert response.headers.get("www-authenticate") == "Bearer", name
        assert response.json()["detail"] in ("Not authenticated", "Invalid or expired token"), name
        if token:
            assert token not in response.text


@pytest.mark.parametrize("route_key", ROLE_LIMITED)
def test_roles_outside_the_matrix_are_refused_before_the_handler(client, make_user, route_key) -> None:
    method, url = _url(route_key)
    allowed = ROLE_GROUPS[EXPECTED_ROUTES[route_key]]
    for role in set(UserRole) - allowed:
        user = make_user(email=f"{role.value.lower()}.denied@example.com", role=role)
        response = client.request(method, url, headers=auth_header(security.create_access_token(user.id).token))
        assert response.status_code == 403, (role, response.text)
        assert response.json() == {"detail": ROLE_DENIED}, role


# --- Layer 2: scenarios ------------------------------------------------------------------------------------------------


def _today() -> date:
    return datetime.now(UTC).astimezone(ORG_TZ).date()


def call(client, method: str, path: str, headers: dict, expected: int | None = None, **body):
    kwargs = {"json": body} if method in ("POST", "PATCH") else {"params": body}
    response = client.request(method, f"/api{path}", headers=headers, **kwargs)
    if expected is not None:
        assert response.status_code == expected, f"{method} {path}: {response.status_code} {response.text}"
    return response


def cycle_dates() -> dict:
    t = _today()
    return {"period_start": (t - timedelta(days=60)).isoformat(), "period_end": (t + timedelta(days=30)).isoformat(),
            "self_assessment_deadline": (t + timedelta(days=10)).isoformat(),
            "manager_review_deadline": (t + timedelta(days=20)).isoformat()}


@pytest.fixture
def world(client, staff, headers_for):
    """People in two departments plus HR and an unlinked ADMIN. Records are created through the API as HR."""
    w = SimpleNamespace()
    w.hr_emp, w.hr = staff(HR, department="People")
    w.mgr_eng, w.mgr_eng_h = staff(MANAGER, department="Engineering")
    w.emp_eng, w.emp_eng_h = staff(EMPLOYEE, department="Engineering")
    w.mgr_sales, w.mgr_sales_h = staff(MANAGER, department="Sales")
    w.emp_sales, w.emp_sales_h = staff(EMPLOYEE, department="Sales")
    w.admin = headers_for(ADMIN)
    w.eng, w.sales = w.emp_eng.department_id, w.emp_sales.department_id
    return w


@pytest.fixture
def sales_records(client, world):
    """A task, intervention, goal and review about the Sales employee, created by HR."""
    w = world
    t = _today()
    w.task = call(client, "POST", "/tasks", w.hr, 201, title="Sales follow-up", assignee_employee_id=w.emp_sales.id).json()["id"]
    w.intervention = call(client, "POST", "/interventions", w.hr, 201, employee_id=w.emp_sales.id,
                          category="MANAGER_CHECK_IN", title="Sales check-in").json()["id"]
    w.goal = call(client, "POST", "/performance/goals", w.hr, 201, employee_id=w.emp_sales.id, title="Sales goal",
                  measurable_target="Ten closed deals", start_date=t.isoformat(),
                  target_date=(t + timedelta(days=30)).isoformat()).json()["id"]
    w.cycle = call(client, "POST", "/performance/cycles", w.hr, 201, name="Matrix cycle", **cycle_dates()).json()["id"]
    call(client, "POST", f"/performance/cycles/{w.cycle}/transition", w.hr, 200, action="open")
    w.review = call(client, "POST", "/performance/reviews", w.hr, 201, cycle_id=w.cycle, employee_id=w.emp_sales.id,
                    reviewer_user_id=w.mgr_sales.user_id).json()["id"]
    return w


def test_employee_reaches_only_their_own_employee_record(client, world) -> None:
    w = world
    call(client, "GET", f"/employees/{w.emp_eng.id}", w.emp_eng_h, 200)
    for other in (w.emp_sales.id, w.mgr_eng.id):  # another department, and a colleague in the same department
        call(client, "GET", f"/employees/{other}", w.emp_eng_h, 404)
    assert [e["id"] for e in call(client, "GET", "/employees", w.emp_eng_h, 200).json()["items"]] == [w.emp_eng.id]
    # Their own record is still not theirs to change.
    call(client, "PATCH", f"/employees/{w.emp_eng.id}", w.emp_eng_h, 403, designation="Chief Executive")
    call(client, "PATCH", f"/employees/{w.emp_eng.id}/status", w.emp_eng_h, 403, status="ACTIVE")


MANAGER_CROSS_DEPARTMENT = [
    ("GET", "/employees/{emp_sales}", {}),
    ("GET", "/ai/attrition/history/{emp_sales}", {}),
    ("POST", "/ai/attrition/predict/{emp_sales}", {}),
    ("GET", "/risk-monitoring/employees/{emp_sales}/history", {}),
    ("GET", "/workforce-analytics/overview", {"department_id": "{sales}"}),
    ("GET", "/workforce-analytics/departments/{sales}", {}),
    ("GET", "/workforce-activity/feed", {"department_id": "{sales}"}),
    ("GET", "/reports/employees", {"department_id": "{sales}"}),
    ("GET", "/reports/attendance", {"department_id": "{sales}"}),
    ("GET", "/audit-logs", {"department_id": "{sales}"}),
    ("POST", "/recommendations/evaluate", {"department_id": "{sales}"}),
    ("POST", "/tasks", {"title": "Cross department", "department_id": "{sales}"}),
    ("POST", "/tasks", {"title": "Cross department", "assignee_employee_id": "{emp_sales}"}),
    ("POST", "/interventions", {"employee_id": "{emp_sales}", "category": "MANAGER_CHECK_IN", "title": "Cross"}),
    ("POST", "/performance/goals", {"employee_id": "{emp_sales}", "title": "Cross goal", "measurable_target": "Target",
                                    "start_date": "{today}", "target_date": "{today}"}),
]


def _fill(value, w):
    if not isinstance(value, str):
        return value
    for key, real in {"{emp_sales}": w.emp_sales.id, "{sales}": w.sales, "{today}": _today().isoformat()}.items():
        if value == key:
            return real
        value = value.replace(key, str(real))
    return value


@pytest.mark.parametrize(("method", "path", "body"), MANAGER_CROSS_DEPARTMENT,
                         ids=[f"{m} {p} {sorted(b)}" for m, p, b in MANAGER_CROSS_DEPARTMENT])
def test_manager_cannot_reach_another_department_by_id_or_filter(client, world, method, path, body) -> None:
    w = world
    response = call(client, method, _fill(path, w), w.mgr_eng_h, **{k: _fill(v, w) for k, v in body.items()})
    assert response.status_code == 404, response.text  # out of scope looks exactly like missing


def test_manager_department_member_list_keeps_its_documented_403(client, world) -> None:
    call(client, "GET", f"/departments/{world.sales}/employees", world.mgr_eng_h, 403)
    call(client, "GET", f"/departments/{world.eng}/employees", world.mgr_eng_h, 200)


def test_records_of_another_department_are_invisible_and_immutable(client, sales_records) -> None:
    w = sales_records
    for headers in (w.mgr_eng_h, w.emp_eng_h):
        call(client, "GET", f"/tasks/{w.task}", headers, 404)
        call(client, "GET", f"/tasks/{w.task}/history", headers, 404)
        call(client, "GET", f"/performance/goals/{w.goal}", headers, 404)
        call(client, "GET", f"/performance/reviews/{w.review}", headers, 404)
        call(client, "GET", f"/performance/reviews/{w.review}/history", headers, 404)
    m = w.mgr_eng_h
    call(client, "PATCH", f"/tasks/{w.task}", m, 404, title="Hijacked")
    call(client, "POST", f"/tasks/{w.task}/assign", m, 404, assignee_employee_id=w.emp_eng.id)
    call(client, "POST", f"/tasks/{w.task}/transition", m, 404, action="cancel", cancellation_reason="Not mine to cancel")
    call(client, "GET", f"/interventions/{w.intervention}", m, 404)
    call(client, "GET", f"/interventions/{w.intervention}/history", m, 404)
    call(client, "POST", f"/interventions/{w.intervention}/assign", m, 404, user_id=w.mgr_eng.user_id)
    call(client, "POST", f"/interventions/{w.intervention}/notes", m, 404, note="Cross-department note")
    call(client, "PATCH", f"/performance/goals/{w.goal}", m, 404, progress_percent=50)
    call(client, "PATCH", f"/performance/reviews/{w.review}", m, 404, reviewer_user_id=w.mgr_eng.user_id)
    call(client, "POST", f"/performance/reviews", m, 404, cycle_id=w.cycle, employee_id=w.emp_sales.id,
         reviewer_user_id=w.mgr_eng.user_id)
    # Linked workflows cannot pull the record in: a task sourced from the Sales intervention is refused.
    call(client, "POST", "/tasks", m, 404, title="Linked", intervention_id=w.intervention)
    # Global search applies the same rules.
    found = call(client, "GET", "/search", m, 200, q="Sales").json()["results"]
    assert {(r["type"], r["id"]) for r in found} & {("task", w.task), ("intervention", w.intervention),
                                                    ("goal", w.goal), ("performance_review", w.review)} == set()


def test_visible_records_reject_actions_outside_the_callers_record_role(client, sales_records) -> None:
    w = sales_records
    e = w.emp_sales_h  # the assignee / goal owner / reviewed employee
    call(client, "PATCH", f"/tasks/{w.task}", e, 403, title="Renamed by assignee")
    call(client, "POST", f"/tasks/{w.task}/assign", e, 403, assignee_employee_id=w.emp_sales.id)
    call(client, "POST", f"/tasks/{w.task}/transition", e, 403, action="cancel", cancellation_reason="Assignee cancel")
    call(client, "PATCH", f"/performance/goals/{w.goal}", e, 403, employee_id=w.emp_eng.id)
    call(client, "PATCH", f"/performance/goals/{w.goal}", e, 403, manager_feedback="Praise for myself")
    call(client, "PATCH", f"/performance/reviews/{w.review}", e, 403, overall_rating=5)
    call(client, "PATCH", f"/performance/reviews/{w.review}", e, 403, reviewer_user_id=w.hr_emp.user_id)
    for action in ("finalize", "cancel"):
        body = {"reason": "Not allowed here"} if action == "cancel" else {}
        call(client, "POST", f"/performance/reviews/{w.review}/{action}", e, 403, **body)
        # The assigned reviewer (the department's manager) cannot finalize or cancel either: ADMIN/HR only.
        call(client, "POST", f"/performance/reviews/{w.review}/{action}", w.mgr_sales_h, 403, **body)
    # Cycles are visible to managers once open, but only ADMIN/HR configure them.
    call(client, "PATCH", f"/performance/cycles/{w.cycle}", w.mgr_sales_h, 403, description="Changed by a manager")
    call(client, "POST", f"/performance/cycles/{w.cycle}/transition", w.mgr_sales_h, 403, action="cancel",
         cancellation_reason="Manager cancel")
    call(client, "PATCH", f"/performance/cycles/{w.cycle}", e, 403, description="Employee with a review in it")
    call(client, "PATCH", f"/performance/cycles/{w.cycle}", w.emp_eng_h, 404, description="Employee without one")


def test_assignments_are_limited_to_eligible_people_in_scope(client, sales_records) -> None:
    w = sales_records
    # A manager of another department can be neither the reviewer nor the intervention owner.
    call(client, "PATCH", f"/performance/reviews/{w.review}", w.hr, 422, reviewer_user_id=w.mgr_eng.user_id)
    call(client, "POST", f"/interventions/{w.intervention}/assign", w.hr, 422, user_id=w.mgr_eng.user_id)
    # Nobody reviews or handles records about themselves.
    call(client, "PATCH", f"/performance/reviews/{w.review}", w.hr, 422, reviewer_user_id=w.emp_sales.user_id)
    call(client, "POST", f"/interventions/{w.intervention}/assign", w.hr, 422, user_id=w.emp_sales.user_id)
    # A manager cannot assign their own department's task to someone in another department.
    own_task = call(client, "POST", "/tasks", w.mgr_eng_h, 201, title="Engineering task").json()["id"]
    call(client, "POST", f"/tasks/{own_task}/assign", w.mgr_eng_h, 404, assignee_employee_id=w.emp_sales.id)
    call(client, "POST", f"/tasks/{own_task}/assign", w.mgr_eng_h, 200, assignee_employee_id=w.emp_eng.id)


PROTECTED_FIELD_INJECTIONS = [
    ("/auth/register", {"full_name": "Mallory", "email": "mallory@example.com", "password": TEST_PASSWORD, "role": "ADMIN"}),
    ("/auth/register", {"full_name": "Mallory", "email": "mallory@example.com", "password": TEST_PASSWORD, "is_active": True}),
    ("/tasks", {"title": "Injected", "status": "COMPLETED"}),
    ("/tasks", {"title": "Injected", "created_by_user_id": 1}),
    ("/performance/goals", {"title": "Injected", "measurable_target": "x" * 5, "start_date": "2026-01-01",
                            "target_date": "2026-02-01", "status": "COMPLETED"}),
    ("/performance/reviews", {"cycle_id": 1, "employee_id": 1, "reviewer_user_id": 1, "status": "FINALIZED"}),
    ("/interventions", {"employee_id": 1, "category": "MANAGER_CHECK_IN", "title": "Injected", "status": "COMPLETED"}),
    ("/leave/requests", {"leave_type_id": 1, "start_date": "2026-03-09", "end_date": "2026-03-09", "reason": "Injected",
                         "employee_id": 1}),
]


@pytest.mark.parametrize(("path", "body"), PROTECTED_FIELD_INJECTIONS, ids=[f"{p}:{sorted(b)[-1]}" for p, b in PROTECTED_FIELD_INJECTIONS])
def test_protected_fields_are_rejected_not_ignored(client, db, world, path, body) -> None:
    response = call(client, "POST", path, world.mgr_eng_h if path != "/auth/register" else {}, **body)
    assert response.status_code == 422, response.text
    assert TEST_PASSWORD not in response.text  # the 422 handler never echoes input
    assert db.scalar(select(User).where(User.email == "mallory@example.com")) is None


def test_employee_record_updates_cannot_carry_a_role(client, world) -> None:
    call(client, "PATCH", f"/employees/{world.emp_eng.id}", world.hr, 422, role="ADMIN")


def test_notifications_belong_to_their_recipient(client, db, world) -> None:
    note = Notification(user_id=world.emp_sales.user_id, category="task", title="Private", message="Only for Sales")
    db.add(note)
    db.commit()
    for headers in (world.emp_eng_h, world.mgr_sales_h, world.hr):
        call(client, "POST", f"/notifications/{note.id}/read", headers, 404)
        assert note.id not in [n["id"] for n in call(client, "GET", "/notifications/me", headers, 200).json()["items"]]
    call(client, "POST", f"/notifications/{note.id}/read", world.emp_sales_h, 200)


PRIVATE_NOTE = "ZZ-private-reviewer-note-ZZ"


def test_unreleased_ratings_and_private_notes_stay_with_reviewers(client, db, sales_records) -> None:
    w = sales_records
    call(client, "POST", f"/performance/reviews/{w.review}/submit-self-assessment", w.emp_sales_h, 200,
         self_assessment="My self-assessment for the matrix test")
    call(client, "PATCH", f"/performance/reviews/{w.review}", w.mgr_sales_h, 200, overall_rating=4,
         manager_summary="Summary", manager_private_notes=PRIVATE_NOTE)
    own = call(client, "GET", f"/performance/reviews/{w.review}", w.emp_sales_h, 200).json()
    assert own["overall_rating"] is None and own["manager_private_notes"] is None
    assert own["manager_evaluation_visible"] is False
    history = call(client, "GET", f"/performance/reviews/{w.review}/history", w.emp_sales_h, 200).text
    assert PRIVATE_NOTE not in history and '"rating"' not in history
    assert call(client, "GET", f"/performance/reviews/{w.review}", w.mgr_sales_h, 200).json()["manager_private_notes"] == PRIVATE_NOTE
    audit_text = " ".join(str(row.details) for row in db.scalars(select(AuditLog)))
    assert PRIVATE_NOTE not in audit_text and "My self-assessment" not in audit_text
    assert PRIVATE_NOTE not in call(client, "GET", "/audit-logs", w.hr, 200).text


def test_employees_cannot_search_restricted_types(client, world) -> None:
    body = call(client, "GET", "/search", world.emp_eng_h, 200, q="Sales", types=["intervention", "recommendation"]).json()
    assert body["searched_types"] == [] and sorted(body["unavailable_types"]) == ["intervention", "recommendation"]


def test_failed_sign_in_audit_never_holds_the_submitted_credentials(client, db, world) -> None:
    secret = "Wrong-Password-For-Audit-1!"
    for email in ("nobody@example.com", "hr.unknown@example.com"):
        assert client.post("/api/auth/login", json={"email": email, "password": secret}).status_code == 401
    rows = " ".join(f"{r.details} {r.entity_id}" for r in db.scalars(select(AuditLog)))
    assert secret not in rows and "nobody@example.com" not in rows


# --- Fixed in Sub-prompt 26: a stale reviewer assignment granted access --------------------------------------------------


def _move(db, employee, **values) -> None:
    for key, value in values.items():
        setattr(employee, key, value)
    db.commit()


def test_reviewer_who_left_the_department_loses_the_review(client, db, sales_records) -> None:
    w = sales_records
    assert call(client, "GET", f"/performance/reviews/{w.review}", w.mgr_sales_h, 200).json()["your_role"] == "reviewer"
    call(client, "PATCH", f"/employees/{w.mgr_sales.id}", w.hr, 200, department_id=w.eng)  # transferred to Engineering

    call(client, "GET", f"/performance/reviews/{w.review}", w.mgr_sales_h, 404)
    call(client, "PATCH", f"/performance/reviews/{w.review}", w.mgr_sales_h, 404, overall_rating=1)
    call(client, "POST", f"/performance/reviews/{w.review}/submit-manager-review", w.mgr_sales_h, 404, overall_rating=1,
         manager_summary="Late")
    assert call(client, "GET", "/performance/reviews", w.mgr_sales_h, 200, to_review=True).json()["total"] == 0
    results = call(client, "GET", "/search", w.mgr_sales_h, 200, q="Matrix", types=["performance_review"]).json()
    assert results["results"] == []
    # HR can hand the review to an eligible reviewer, who then sees it.
    call(client, "PATCH", f"/performance/reviews/{w.review}", w.hr, 200, reviewer_user_id=w.hr_emp.user_id)


def test_reviewer_who_lost_the_manager_role_loses_the_review(client, db, sales_records) -> None:
    w = sales_records
    reviewer = db.get(User, w.mgr_sales.user_id)
    reviewer.role = EMPLOYEE  # roles change outside the API (there is no role-management endpoint)
    db.commit()
    call(client, "GET", f"/performance/reviews/{w.review}", w.mgr_sales_h, 404)
    call(client, "PATCH", f"/performance/reviews/{w.review}", w.mgr_sales_h, 404, manager_private_notes="Still here?")


# --- Fixed in Sub-prompt 26: a departed (INACTIVE) manager kept department scope -----------------------------------------


@pytest.fixture
def pending_leave(client, world, make_leave_type, make_policy, leave_clock):
    leave_type = make_leave_type("ANNUAL", "Annual Leave")
    make_policy(leave_type, annual_allowance_days=10)
    request = call(client, "POST", "/leave/requests", world.emp_eng_h, 201, leave_type_id=leave_type.id,
                   start_date="2026-03-09", end_date="2026-03-10", reason="Family commitments").json()
    return SimpleNamespace(id=request["id"], leave_type_id=leave_type.id)


def test_inactive_manager_keeps_self_service_but_loses_the_department(client, db, world, pending_leave) -> None:
    w = world
    task = call(client, "POST", "/tasks", w.mgr_eng_h, 201, title="Before leaving", assignee_employee_id=w.emp_eng.id).json()["id"]
    call(client, "PATCH", f"/employees/{w.mgr_eng.id}/status", w.hr, 200, status="INACTIVE", reason="Left the company")

    m = w.mgr_eng_h
    call(client, "GET", f"/employees/{w.emp_eng.id}", m, 404)
    assert [e["id"] for e in call(client, "GET", "/employees", m, 200).json()["items"]] == [w.mgr_eng.id]
    assert call(client, "GET", "/leave/requests", m, 200).json()["total"] == 0
    call(client, "POST", f"/leave/requests/{pending_leave.id}/approve", m, 404)
    call(client, "GET", f"/tasks/{task}", m, 404)
    call(client, "POST", "/tasks", m, 403, title="After leaving")
    call(client, "GET", f"/departments/{w.eng}/employees", m, 403)
    for path in ("/reports/employees", "/audit-logs", "/workforce-analytics/overview", "/workforce-activity/feed"):
        call(client, "GET", path, m, 403)
    call(client, "POST", "/interventions", m, 404, employee_id=w.emp_eng.id, category="MANAGER_CHECK_IN", title="After")
    # Their own records stay reachable.
    call(client, "GET", f"/employees/{w.mgr_eng.id}", m, 200)
    call(client, "GET", "/leave/balances/me", m, 200)
    # They are no longer an eligible reviewer, and are not notified about their former department.
    cycle = call(client, "POST", "/performance/cycles", w.hr, 201, name="After departure", **cycle_dates()).json()["id"]
    call(client, "POST", f"/performance/cycles/{cycle}/transition", w.hr, 200, action="open")
    call(client, "POST", "/performance/reviews", w.hr, 422, cycle_id=cycle, employee_id=w.emp_eng.id,
         reviewer_user_id=w.mgr_eng.user_id)
    before = len(db.scalars(select(Notification).where(Notification.user_id == w.mgr_eng.user_id)).all())
    call(client, "POST", "/leave/requests", w.emp_eng_h, 201, leave_type_id=pending_leave.leave_type_id,
         start_date="2026-03-16", end_date="2026-03-16", reason="Another family day")
    assert len(db.scalars(select(Notification).where(Notification.user_id == w.mgr_eng.user_id)).all()) == before


def test_manager_on_leave_keeps_department_scope(client, world, pending_leave) -> None:
    """Only INACTIVE (departed) removes the scope; ON_LEAVE is still employed."""
    w = world
    call(client, "PATCH", f"/employees/{w.mgr_eng.id}/status", w.hr, 200, status="ON_LEAVE")
    call(client, "GET", f"/employees/{w.emp_eng.id}", w.mgr_eng_h, 200)
    call(client, "POST", f"/leave/requests/{pending_leave.id}/approve", w.mgr_eng_h, 200)
