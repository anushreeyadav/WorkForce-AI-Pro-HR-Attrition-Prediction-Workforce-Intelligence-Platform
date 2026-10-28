from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.models import AuditLog, Employee, EmploymentStatus, UserRole
from tests.conftest import auth_header

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
ALL_ROLES = list(UserRole)
WRITERS = {ADMIN, HR}

EMPLOYEE_FIELDS = {
    "id", "employee_code", "full_name", "work_email", "designation", "date_of_joining", "status",
    "phone", "user_id", "department", "created_at", "updated_at",
}


def new_employee(department_id: int, **overrides) -> dict:
    payload = {
        "employee_code": "emp-100",
        "full_name": "  Priya Sharma ",
        "work_email": "Priya.Sharma@Corp.Example.com",
        "department_id": department_id,
        "designation": "Data Analyst",
        "date_of_joining": "2025-03-01",
    }
    payload.update(overrides)
    return payload


def items(response) -> list[str]:
    return [e["employee_code"] for e in response.json()["items"]]


# --- Create -----------------------------------------------------------------------------------------


def test_create_employee_normalizes_and_audits(client, headers_for, make_department, db) -> None:
    department = make_department("Analytics")
    response = client.post(
        "/api/employees", json=new_employee(department.id, phone="+91 98765 43210"), headers=headers_for(HR)
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == EMPLOYEE_FIELDS
    assert body["employee_code"] == "EMP-100"
    assert body["full_name"] == "Priya Sharma"
    assert body["work_email"] == "priya.sharma@corp.example.com"
    assert body["status"] == "ACTIVE"
    assert body["department"] == {"id": department.id, "name": "Analytics"}
    assert body["user_id"] is None

    log = db.scalar(select(AuditLog).where(AuditLog.action == "employee.created"))
    assert log.entity_id == str(body["id"])
    assert log.details == {"employee_code": "EMP-100", "department_id": department.id}


def test_duplicate_code_and_email_409(client, headers_for, make_department) -> None:
    department = make_department()
    headers = headers_for(ADMIN)
    assert client.post("/api/employees", json=new_employee(department.id), headers=headers).status_code == 201

    same_code = client.post(
        "/api/employees", json=new_employee(department.id, employee_code="EMP-100", work_email="other@corp.example.com"),
        headers=headers,
    )
    assert same_code.status_code == 409
    assert same_code.json() == {"detail": "Employee code is already in use"}

    same_email = client.post(
        "/api/employees", json=new_employee(department.id, employee_code="EMP-200", work_email="PRIYA.SHARMA@corp.example.com"),
        headers=headers,
    )
    assert same_email.status_code == 409
    assert same_email.json() == {"detail": "Work email is already in use"}


def test_department_reference_validated(client, headers_for, make_department) -> None:
    closed = make_department("Closed", is_active=False)
    headers = headers_for(ADMIN)

    missing = client.post("/api/employees", json=new_employee(999), headers=headers)
    assert missing.status_code == 422
    assert missing.json() == {"detail": "Department does not exist"}

    inactive = client.post("/api/employees", json=new_employee(closed.id), headers=headers)
    assert inactive.status_code == 422
    assert inactive.json() == {"detail": "Department is inactive"}


def test_user_link_validation(client, headers_for, make_user, make_department, make_employee) -> None:
    department = make_department()
    linked_user = make_user(email="linked@example.com")
    free_user = make_user(email="free@example.com")
    make_employee(department, user_id=linked_user.id)
    headers = headers_for(ADMIN)

    assert client.post("/api/employees", json=new_employee(department.id, user_id=9999), headers=headers).status_code == 422
    taken = client.post("/api/employees", json=new_employee(department.id, user_id=linked_user.id), headers=headers)
    assert taken.status_code == 409
    ok = client.post("/api/employees", json=new_employee(department.id, user_id=free_user.id), headers=headers)
    assert ok.status_code == 201
    # Only the id is exposed; no account details or password hash.
    assert ok.json()["user_id"] == free_user.id
    assert "$2b$" not in ok.text and "password" not in ok.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"employee_code": "E"},
        {"employee_code": "EMP 100"},
        {"employee_code": "EMP_100"},
        {"full_name": "   "},
        {"full_name": "x" * 151},
        {"work_email": "not-an-email"},
        {"designation": ""},
        {"date_of_joining": "2025-02-30"},
        {"date_of_joining": "1900-01-01"},
        {"date_of_joining": (date.today() + timedelta(days=400)).isoformat()},
        {"phone": "call me"},
        {"phone": "12"},
        {"status": "FIRED"},
        {"department_id": 0},
        {"salary": 100000},
        {"password_hash": "x"},
        {"id": 77},
    ],
    ids=["code-short", "code-space", "code-underscore", "blank-name", "long-name", "bad-email",
         "blank-designation", "impossible-date", "too-early", "too-far-future", "phone-text",
         "phone-short", "bad-status", "zero-department", "salary-field", "hash-field", "id-field"],
)
def test_create_validation_422(client, headers_for, make_department, overrides: dict) -> None:
    payload = {**new_employee(make_department().id), **overrides}
    response = client.post("/api/employees", json=payload, headers=headers_for(ADMIN))
    assert response.status_code == 422


@pytest.mark.parametrize("missing", ["employee_code", "full_name", "work_email", "department_id", "designation", "date_of_joining"])
def test_create_required_fields(client, headers_for, make_department, missing: str) -> None:
    payload = new_employee(make_department().id)
    del payload[missing]
    assert client.post("/api/employees", json=payload, headers=headers_for(ADMIN)).status_code == 422


# --- List, filters, search --------------------------------------------------------------------------


@pytest.fixture
def directory(make_department, make_employee, make_user):
    """A small, varied set of employees across two departments."""
    engineering = make_department("Engineering")
    sales = make_department("Sales")
    active_user = make_user(email="active.user@example.com")
    inactive_user = make_user(email="inactive.user@example.com", is_active=False)
    make_employee(engineering, employee_code="ENG-1", full_name="Asha Rao", designation="Backend Engineer",
                  date_of_joining=date(2023, 1, 10), user_id=active_user.id)
    make_employee(engineering, employee_code="ENG-2", full_name="Ben Cole", designation="Frontend Engineer",
                  date_of_joining=date(2024, 6, 1), status=EmploymentStatus.ON_LEAVE, user_id=inactive_user.id)
    make_employee(sales, employee_code="SAL-1", full_name="Cara Diaz", designation="Account Executive",
                  date_of_joining=date(2025, 2, 15), work_email="cara_diaz@corp.example.com")
    make_employee(sales, employee_code="SAL-2", full_name="Dev 100% Patel", designation="Sales Manager",
                  date_of_joining=date(2022, 11, 30), status=EmploymentStatus.INACTIVE)
    return {"engineering": engineering, "sales": sales}


def test_list_default_sort_and_pagination(client, headers_for, directory) -> None:
    headers = headers_for(HR)
    first = client.get("/api/employees", params={"page_size": 3}, headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert {k: body[k] for k in ("total", "page", "page_size", "pages")} == {"total": 4, "page": 1, "page_size": 3, "pages": 2}
    assert items(first) == ["ENG-1", "ENG-2", "SAL-1"]  # by full_name
    assert items(client.get("/api/employees", params={"page": 2, "page_size": 3}, headers=headers)) == ["SAL-2"]


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"department_id": "ENG"}, None),
        ({"status": "ON_LEAVE"}, ["ENG-2"]),
        ({"status": "INACTIVE"}, ["SAL-2"]),
        ({"designation": "backend engineer"}, ["ENG-1"]),
        ({"designation": "Engineer"}, []),  # exact match, not substring
        ({"joined_from": "2024-01-01"}, ["ENG-2", "SAL-1"]),
        ({"joined_to": "2023-01-10"}, ["ENG-1", "SAL-2"]),
        ({"joined_from": "2023-01-01", "joined_to": "2024-12-31"}, ["ENG-1", "ENG-2"]),
        ({"user_active": "true"}, ["ENG-1"]),
        ({"user_active": "false"}, ["ENG-2"]),
        ({"q": "eng"}, ["ENG-1", "ENG-2"]),
        ({"q": "CARA"}, ["SAL-1"]),
        ({"q": "account exec"}, ["SAL-1"]),
        ({"q": "corp.example"}, ["ENG-1", "ENG-2", "SAL-1", "SAL-2"]),
        ({"q": "100%"}, ["SAL-2"]),
        ({"q": "%"}, ["SAL-2"]),  # literal percent, not a wildcard
        ({"q": "a_d"}, ["SAL-1"]),  # literal underscore: matches "cara_diaz" only
        ({"q": "' OR 1=1 --"}, []),
        ({"status": "ACTIVE", "q": "sal"}, ["SAL-1"]),
        ({"sort_by": "date_of_joining", "sort_order": "desc"}, ["SAL-1", "ENG-2", "ENG-1", "SAL-2"]),
        ({"sort_by": "employee_code"}, ["ENG-1", "ENG-2", "SAL-1", "SAL-2"]),
    ],
)
def test_list_filters_and_search(client, headers_for, directory, params: dict, expected) -> None:
    if params.get("department_id") == "ENG":
        params = {"department_id": directory["engineering"].id}
        expected = ["ENG-1", "ENG-2"]
    response = client.get("/api/employees", params=params, headers=headers_for(ADMIN))
    assert response.status_code == 200
    assert items(response) == expected


@pytest.mark.parametrize(
    "params",
    [
        {"joined_from": "2025-01-01", "joined_to": "2024-01-01"},
        {"joined_from": "yesterday"},
        {"status": "RETIRED"},
        {"sort_by": "password_hash"},
        {"sort_order": "sideways"},
        {"page_size": 500},
        {"department_id": -1},
        {"q": "x" * 101},
        {"salary_min": 10},
    ],
    ids=["inverted-range", "bad-date", "bad-status", "bad-sort", "bad-order", "page-size", "bad-dept",
         "long-q", "unknown-param"],
)
def test_list_invalid_params_422(client, headers_for, params: dict) -> None:
    assert client.get("/api/employees", params=params, headers=headers_for(ADMIN)).status_code == 422


def test_search_endpoint_requires_q(client, headers_for, directory) -> None:
    headers = headers_for(ADMIN)
    assert client.get("/api/employees/search", headers=headers).status_code == 422
    assert client.get("/api/employees/search", params={"q": "a"}, headers=headers).status_code == 422

    response = client.get(
        "/api/employees/search", params={"q": "engineer", "status": "ACTIVE"}, headers=headers
    )
    assert response.status_code == 200
    assert items(response) == ["ENG-1"]


def test_list_avoids_n_plus_one(client, headers_for, make_department, make_employee, count_queries) -> None:
    headers = headers_for(ADMIN)
    make_employee(make_department("Dept 0"))

    before = len(count_queries())
    client.get("/api/employees", headers=headers)
    one = len(count_queries()) - before

    for i in range(1, 6):
        department = make_department(f"Dept {i}")
        make_employee(department)
        make_employee(department)

    before = len(count_queries())
    body = client.get("/api/employees", headers=headers).json()
    many = len(count_queries()) - before

    assert body["total"] == 11
    assert many == one


# --- Detail and role scoping -------------------------------------------------------------------------


def test_get_employee_and_404(client, headers_for, make_department, make_employee) -> None:
    employee = make_employee(make_department())
    headers = headers_for(HR)
    response = client.get(f"/api/employees/{employee.id}", headers=headers)
    assert response.status_code == 200
    assert set(response.json()) == EMPLOYEE_FIELDS
    assert client.get("/api/employees/999", headers=headers).status_code == 404
    assert client.get("/api/employees/not-a-number", headers=headers).status_code == 422


@pytest.fixture
def scoped(make_user, make_department, make_employee):
    engineering = make_department("Engineering")
    sales = make_department("Sales")
    manager_user = make_user(email="manager@example.com", role=MANAGER)
    staff_user = make_user(email="staff@example.com", role=EMPLOYEE)
    manager = make_employee(engineering, employee_code="MGR-1", user_id=manager_user.id)
    staff = make_employee(engineering, employee_code="ENG-9", user_id=staff_user.id)
    peer = make_employee(engineering, employee_code="ENG-8")
    outsider = make_employee(sales, employee_code="SAL-9")
    return {
        "manager_headers": auth_header(create_access_token(manager_user.id).token),
        "staff_headers": auth_header(create_access_token(staff_user.id).token),
        "manager": manager, "staff": staff, "peer": peer, "outsider": outsider,
    }


def test_manager_scope(client, scoped) -> None:
    headers = scoped["manager_headers"]
    assert sorted(items(client.get("/api/employees", headers=headers))) == ["ENG-8", "ENG-9", "MGR-1"]
    assert client.get(f"/api/employees/{scoped['peer'].id}", headers=headers).status_code == 200
    # Out-of-scope records are indistinguishable from missing ones.
    hidden = client.get(f"/api/employees/{scoped['outsider'].id}", headers=headers)
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "Employee not found"}
    # Asking for another department's employees explicitly still returns nothing.
    other = client.get("/api/employees", params={"department_id": scoped["outsider"].department_id}, headers=headers)
    assert other.json()["total"] == 0
    assert items(client.get("/api/employees/search", params={"q": "SAL"}, headers=headers)) == []


def test_employee_sees_only_own_record(client, scoped) -> None:
    headers = scoped["staff_headers"]
    assert items(client.get("/api/employees", headers=headers)) == ["ENG-9"]
    assert client.get(f"/api/employees/{scoped['staff'].id}", headers=headers).status_code == 200
    for other in ("peer", "manager", "outsider"):
        assert client.get(f"/api/employees/{scoped[other].id}", headers=headers).status_code == 404


@pytest.mark.parametrize("role", [MANAGER, EMPLOYEE])
def test_unlinked_users_see_nothing(client, headers_for, make_department, make_employee, role) -> None:
    employee = make_employee(make_department())
    headers = headers_for(role)
    assert client.get("/api/employees", headers=headers).json()["total"] == 0
    assert client.get(f"/api/employees/{employee.id}", headers=headers).status_code == 404


# --- Update -------------------------------------------------------------------------------------------


def test_patch_partial_update_and_audit(client, headers_for, make_department, make_employee, db) -> None:
    employee = make_employee(make_department(), designation="Engineer", phone="+1 555 0100")
    response = client.patch(
        f"/api/employees/{employee.id}", json={"designation": "Senior Engineer"}, headers=headers_for(HR)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["designation"] == "Senior Engineer"
    assert body["phone"] == "+1 555 0100"  # omitted fields untouched
    assert body["employee_code"] == employee.employee_code

    log = db.scalar(select(AuditLog).where(AuditLog.action == "employee.updated"))
    assert log.details == {"changes": {"designation": {"from": "Engineer", "to": "Senior Engineer"}}}


def test_patch_department_change_reflected_in_response(client, headers_for, make_department, make_employee) -> None:
    old = make_department("Engineering")
    new = make_department("Platform")
    employee = make_employee(old)
    response = client.patch(f"/api/employees/{employee.id}", json={"department_id": new.id}, headers=headers_for(ADMIN))
    assert response.status_code == 200
    assert response.json()["department"] == {"id": new.id, "name": "Platform"}


def test_patch_nullable_fields_can_be_cleared(client, headers_for, make_user, make_department, make_employee) -> None:
    user = make_user(email="link@example.com")
    employee = make_employee(make_department(), phone="+1 555 0100", user_id=user.id)
    body = client.patch(
        f"/api/employees/{employee.id}", json={"phone": None, "user_id": None}, headers=headers_for(ADMIN)
    ).json()
    assert body["phone"] is None
    assert body["user_id"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"full_name": None},
        {"employee_code": None},
        {"department_id": None},
        {"status": "INACTIVE"},  # status changes go through /status
        {"salary": 5},
        {"work_email": "bad"},
    ],
    ids=["empty", "null-name", "null-code", "null-department", "status-field", "salary", "bad-email"],
)
def test_patch_invalid_422(client, headers_for, make_department, make_employee, payload: dict) -> None:
    employee = make_employee(make_department())
    assert client.patch(f"/api/employees/{employee.id}", json=payload, headers=headers_for(ADMIN)).status_code == 422


def test_patch_conflicts(client, headers_for, make_department, make_employee) -> None:
    department = make_department()
    first = make_employee(department, employee_code="EMP-1", work_email="one@corp.example.com")
    make_employee(department, employee_code="EMP-2", work_email="two@corp.example.com")
    headers = headers_for(ADMIN)

    assert client.patch(f"/api/employees/{first.id}", json={"employee_code": "emp-2"}, headers=headers).status_code == 409
    assert client.patch(f"/api/employees/{first.id}", json={"work_email": "TWO@corp.example.com"}, headers=headers).status_code == 409
    # Re-sending its own values is not a conflict.
    assert client.patch(f"/api/employees/{first.id}", json={"employee_code": "EMP-1"}, headers=headers).status_code == 200


def test_patch_to_inactive_department_rejected(client, headers_for, make_department, make_employee) -> None:
    employee = make_employee(make_department("Active"))
    closed = make_department("Closed", is_active=False)
    response = client.patch(f"/api/employees/{employee.id}", json={"department_id": closed.id}, headers=headers_for(ADMIN))
    assert response.status_code == 422


def test_patch_404(client, headers_for) -> None:
    assert client.patch("/api/employees/999", json={"designation": "X"}, headers=headers_for(ADMIN)).status_code == 404


# --- Status -----------------------------------------------------------------------------------------


def test_status_change_with_reason_is_audited(client, headers_for, make_department, make_employee, db) -> None:
    employee = make_employee(make_department())
    response = client.patch(
        f"/api/employees/{employee.id}/status",
        json={"status": "ON_LEAVE", "reason": "Parental leave"},
        headers=headers_for(HR),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ON_LEAVE"

    log = db.scalar(select(AuditLog).where(AuditLog.action == "employee.status_changed"))
    assert log.details == {
        "changes": {"status": {"from": "ACTIVE", "to": "ON_LEAVE"}},
        "reason": "Parental leave",
    }


def test_same_status_is_noop(client, headers_for, make_department, make_employee, db) -> None:
    employee = make_employee(make_department())
    response = client.patch(f"/api/employees/{employee.id}/status", json={"status": "ACTIVE"}, headers=headers_for(HR))
    assert response.status_code == 200
    assert db.scalar(select(AuditLog).where(AuditLog.action == "employee.status_changed")) is None


@pytest.mark.parametrize("payload", [{}, {"status": "FIRED"}, {"status": "INACTIVE", "reason": "r" * 256}, {"status": "INACTIVE", "x": 1}])
def test_status_invalid_422(client, headers_for, make_department, make_employee, payload: dict) -> None:
    employee = make_employee(make_department())
    assert client.patch(f"/api/employees/{employee.id}/status", json=payload, headers=headers_for(HR)).status_code == 422


def test_no_hard_delete(client, headers_for, make_department, make_employee, db) -> None:
    employee = make_employee(make_department())
    assert client.delete(f"/api/employees/{employee.id}", headers=headers_for(ADMIN)).status_code == 405
    assert db.get(Employee, employee.id) is not None


# --- RBAC --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ALL_ROLES, ids=[r.value for r in ALL_ROLES])
def test_write_rbac(client, headers_for, make_department, make_employee, role: UserRole) -> None:
    department = make_department()
    employee = make_employee(department)
    headers = headers_for(role)
    allowed = role in WRITERS

    create = client.post("/api/employees", json=new_employee(department.id), headers=headers)
    update = client.patch(f"/api/employees/{employee.id}", json={"designation": "Lead"}, headers=headers)
    status_change = client.patch(f"/api/employees/{employee.id}/status", json={"status": "INACTIVE"}, headers=headers)

    assert create.status_code == (201 if allowed else 403)
    assert update.status_code == (200 if allowed else 403)
    assert status_change.status_code == (200 if allowed else 403)


@pytest.mark.parametrize(
    "method, path",
    [("get", "/api/employees"), ("post", "/api/employees"), ("get", "/api/employees/search?q=ab"),
     ("get", "/api/employees/1"), ("patch", "/api/employees/1"), ("patch", "/api/employees/1/status")],
)
def test_requires_authentication(client, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401
