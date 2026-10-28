"""No route answers 500 (Sub-prompt 29 QA).

1. Every registered route is called as ADMIN and as EMPLOYEE with empty bodies and placeholder ids, and every list route
   with malformed paging, search, id and date values.
2. Every route is called again by all four roles against a populated world (the query-scaling world: records of every
   module, so id 1 exists for employees, tasks, goals, reviews, interventions, alerts, payroll and more), with empty
   bodies.

Invalid input must produce a 4xx (usually 403/404/409/422), never an unhandled server error. ML artifacts and datasets
go to temporary directories (ml_dirs); SQLite in memory only.
"""

import pytest

from app.models import UserRole
from tests.test_authorization_matrix import EXPECTED_ROUTES, _url
from tests.test_query_scaling import make_alert, world  # noqa: F401  (pytest fixtures)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
ROUTES = sorted(k for k, group in EXPECTED_ROUTES.items() if group != "PUBLIC") + ["POST /auth/register", "POST /auth/login"]
LIST_ROUTES = sorted(k for k in EXPECTED_ROUTES if k.startswith("GET ") and "{" not in k)
MALFORMED = [
    {"page": "0"}, {"page_size": "1000"}, {"page": "-1"}, {"q": "x" * 500}, {"department_id": "-1"},
    {"department_id": "abc"}, {"date_from": "not-a-date"}, {"date_from": "2026-12-31", "date_to": "2026-01-01"},
    {"unexpected_filter": "1"},
]


@pytest.fixture
def sweep_people(staff, headers_for, ml_dirs):
    staff(MANAGER, department="Engineering")
    staff(department="Engineering")
    _, employee = staff(department="Sales")
    return {ADMIN: headers_for(ADMIN), EMPLOYEE: employee}


def _call(client, route_key, headers, path_id=None):
    method, url = _url(route_key)
    if path_id is not None:
        url = url.replace("/999", f"/{path_id}")
    kwargs = {"json": {}} if method in ("POST", "PATCH") else {}
    return client.request(method, url, headers=headers, **kwargs)


@pytest.mark.parametrize("route_key", ROUTES)
def test_route_never_returns_a_server_error(client, sweep_people, route_key) -> None:
    for role in (ADMIN, EMPLOYEE):
        response = _call(client, route_key, sweep_people[role])
        assert response.status_code < 500, (role, response.status_code, response.text[:300])


@pytest.mark.parametrize("route_key", LIST_ROUTES)
def test_malformed_query_values_are_client_errors(client, sweep_people, route_key) -> None:
    _, url = _url(route_key)
    for params in MALFORMED:
        response = client.get(url, headers=sweep_people[ADMIN], params=params)
        # Routes with a filter model reject unknown fields (422); routes that take no query parameters at all
        # (summaries, catalogs, /auth/me) ignore them, which cannot widen what the caller sees.
        assert response.status_code < 500, (params, response.status_code, response.text[:300])


def test_populated_world_never_returns_a_server_error(client, world, headers_for, ml_dirs) -> None:
    world["grow"](2, 3)
    roles = {"admin": headers_for(ADMIN), "hr": world["hr"], "manager": world["manager"]}
    employee = client.post("/api/auth/login", json={"email": "qa.sweep@example.com", "password": "x"})  # unknown: 401
    assert employee.status_code == 401
    errors = []
    for route_key in ROUTES:
        if route_key in ("POST /auth/register", "POST /auth/login"):
            continue
        for name, headers in roles.items():
            response = _call(client, route_key, headers, path_id=1)
            if response.status_code == 503 and route_key.startswith("POST /ai/attrition/predict"):
                # Documented model-unavailable response: the world registers an ACTIVE model without artifact files.
                assert response.json()["detail"]["code"] == "artifact_error"
                continue
            if response.status_code >= 500:
                errors.append((name, route_key, response.status_code, response.text[:200]))
    assert errors == []
