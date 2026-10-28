"""The frontend's role navigation matches the backend role matrix (Sub-prompt 30).

frontend/src/lib/navigation.js drives the sidebar and the route guards. Each page is offered to exactly the roles the
backend allows on the endpoint that page is built on, so no role sees a page that can only answer 403, and no permitted
page is hidden. The backend remains the security boundary; this test keeps the two in step.
"""

import re
from pathlib import Path

from tests.test_authorization_matrix import EXPECTED_ROUTES, ROLE_GROUPS

NAVIGATION = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "navigation.js"
ALL = frozenset({"ADMIN", "HR", "MANAGER", "EMPLOYEE"})
CONSTANTS = {"ALL": ALL, "STAFF": frozenset({"ADMIN", "HR", "MANAGER"}), "HR_ADMIN": frozenset({"ADMIN", "HR"})}

# Page -> the backend endpoint its main content comes from.
PAGE_ENDPOINT = {
    "/": "GET /tasks/summary", "/search": "GET /search", "/notifications": "GET /notifications/me",
    "/employees": "GET /employees", "/departments": "GET /departments", "/attendance": "GET /attendance/me",
    "/leave": "GET /leave/requests/me", "/payslips": "GET /payroll/me", "/payroll": "GET /payroll/periods",
    "/risk": "GET /risk-monitoring/alerts", "/analytics": "GET /workforce-analytics/overview",
    "/recommendations": "GET /recommendations", "/interventions": "GET /interventions", "/tasks": "GET /tasks",
    "/performance": "GET /performance/goals", "/activity": "GET /workforce-activity/feed", "/reports": "GET /reports/employees",
    "/audit": "GET /audit-logs", "/datasets": "GET /datasets",
}


def _pages() -> dict[str, frozenset]:
    source = NAVIGATION.read_text(encoding="utf-8")
    pages = {path: CONSTANTS[roles] for path, roles in re.findall(r"\{\s*to:\s*'([^']+)'[^}]*roles:\s*(\w+)\s*\}", source)}
    assert len(pages) == 19, pages
    return pages


def _allowed(route_key: str) -> frozenset:
    roles = ROLE_GROUPS[EXPECTED_ROUTES[route_key]]
    return ALL if roles is None else frozenset(r.value for r in roles)  # AUTHENTICATED: every signed-in role


def test_every_page_is_mapped_to_a_backend_endpoint() -> None:
    assert set(_pages()) == set(PAGE_ENDPOINT)
    assert set(PAGE_ENDPOINT.values()) <= set(EXPECTED_ROUTES)


def test_page_roles_match_backend_roles() -> None:
    mismatches = {path: (sorted(roles), sorted(_allowed(PAGE_ENDPOINT[path])))
                  for path, roles in _pages().items() if roles != _allowed(PAGE_ENDPOINT[path])}
    assert mismatches == {}
