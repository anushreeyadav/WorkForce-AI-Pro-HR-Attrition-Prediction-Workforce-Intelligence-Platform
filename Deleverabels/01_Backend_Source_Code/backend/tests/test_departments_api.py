import pytest
from sqlalchemy import select

from app.models import AuditLog, EmploymentStatus, UserRole

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
ALL_ROLES = list(UserRole)
WRITERS = {ADMIN, HR}


# --- Create -----------------------------------------------------------------------------------------


def test_create_department_trims_and_audits(client, headers_for, db) -> None:
    response = client.post(
        "/api/departments",
        json={"name": "  Human   Resources ", "description": "  People team  "},
        headers=headers_for(HR),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Human Resources"
    assert body["description"] == "People team"
    assert body["is_active"] is True
    assert body["employee_count"] == 0
    assert set(body) == {"id", "name", "description", "is_active", "employee_count", "created_at", "updated_at"}

    log = db.scalar(select(AuditLog).where(AuditLog.action == "department.created"))
    assert log.entity_id == str(body["id"])
    assert log.details == {"name": "Human Resources"}


@pytest.mark.parametrize("name", ["engineering", "  ENGINEERING ", "Engineering"])
def test_duplicate_name_case_insensitive_409(client, headers_for, make_department, name: str) -> None:
    make_department("Engineering")
    response = client.post("/api/departments", json={"name": name}, headers=headers_for(ADMIN))
    assert response.status_code == 409
    assert response.json() == {"detail": "A department with this name already exists"}


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "A"},
        {"name": "x" * 101},
        {"name": "<script>alert(1)</script>"},
        {"name": "-Leading dash"},
        {"name": None},
        {},
        {"name": "Finance", "description": "d" * 1001},
        {"name": "Finance", "budget": 100},
    ],
    ids=["empty", "blank", "too-short", "too-long", "markup", "leading-punct", "null", "missing",
         "long-description", "extra-field"],
)
def test_invalid_department_payload_422(client, headers_for, payload: dict) -> None:
    assert client.post("/api/departments", json=payload, headers=headers_for(ADMIN)).status_code == 422


@pytest.mark.parametrize("name", ["R&D", "Sales / Marketing", "Customer Success (EMEA)", "Ingeniería", "Ops-2"])
def test_valid_department_names(client, headers_for, name: str) -> None:
    assert client.post("/api/departments", json={"name": name}, headers=headers_for(ADMIN)).status_code == 201


# --- List / detail ------------------------------------------------------------------------------------


def test_list_pagination_metadata_and_order(client, headers_for, make_department) -> None:
    for name in ("Sales", "Engineering", "Finance"):
        make_department(name)
    headers = headers_for(EMPLOYEE)

    first = client.get("/api/departments", params={"page": 1, "page_size": 2}, headers=headers).json()
    assert {k: first[k] for k in ("total", "page", "page_size", "pages")} == {
        "total": 3, "page": 1, "page_size": 2, "pages": 2,
    }
    assert [d["name"] for d in first["items"]] == ["Engineering", "Finance"]

    second = client.get("/api/departments", params={"page": 2, "page_size": 2}, headers=headers).json()
    assert [d["name"] for d in second["items"]] == ["Sales"]

    beyond = client.get("/api/departments", params={"page": 5, "page_size": 2}, headers=headers).json()
    assert beyond["items"] == [] and beyond["total"] == 3


def test_empty_list_has_zero_pages(client, headers_for) -> None:
    body = client.get("/api/departments", headers=headers_for(ADMIN)).json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}


def test_list_filter_by_active_status(client, headers_for, make_department) -> None:
    make_department("Active Dept")
    make_department("Closed Dept", is_active=False)
    headers = headers_for(ADMIN)

    active = client.get("/api/departments", params={"is_active": "true"}, headers=headers).json()
    inactive = client.get("/api/departments", params={"is_active": "false"}, headers=headers).json()
    everything = client.get("/api/departments", headers=headers).json()

    assert [d["name"] for d in active["items"]] == ["Active Dept"]
    assert [d["name"] for d in inactive["items"]] == ["Closed Dept"]
    assert everything["total"] == 2


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"is_active": "maybe"}, {"unknown": "x"}],
)
def test_invalid_list_params_422(client, headers_for, params: dict) -> None:
    assert client.get("/api/departments", params=params, headers=headers_for(ADMIN)).status_code == 422


def test_detail_includes_employee_count(client, headers_for, make_department, make_employee) -> None:
    engineering = make_department("Engineering")
    sales = make_department("Sales")
    make_employee(engineering)
    make_employee(engineering, status=EmploymentStatus.ON_LEAVE)
    make_employee(sales)

    body = client.get(f"/api/departments/{engineering.id}", headers=headers_for(EMPLOYEE)).json()
    assert body["employee_count"] == 2
    listed = client.get("/api/departments", headers=headers_for(HR, "hr2@example.com")).json()["items"]
    assert {d["name"]: d["employee_count"] for d in listed} == {"Engineering": 2, "Sales": 1}


def test_detail_404(client, headers_for) -> None:
    response = client.get("/api/departments/999", headers=headers_for(ADMIN))
    assert response.status_code == 404
    assert response.json() == {"detail": "Department not found"}


def test_list_avoids_n_plus_one(client, headers_for, make_department, make_employee, count_queries) -> None:
    headers = headers_for(ADMIN)
    department = make_department("Dept 0")
    make_employee(department)

    before = len(count_queries())
    client.get("/api/departments", headers=headers)
    one_department = len(count_queries()) - before

    for i in range(1, 8):
        extra = make_department(f"Dept {i}")
        make_employee(extra)
        make_employee(extra)

    before = len(count_queries())
    body = client.get("/api/departments", headers=headers).json()
    many_departments = len(count_queries()) - before

    assert body["total"] == 8
    assert many_departments == one_department  # constant number of statements


# --- Update -------------------------------------------------------------------------------------------


def test_patch_updates_only_sent_fields_and_audits(client, headers_for, make_department, db) -> None:
    department = make_department("Engineering")
    headers = headers_for(ADMIN)
    client.patch(f"/api/departments/{department.id}", json={"description": "Builds things"}, headers=headers)

    response = client.patch(f"/api/departments/{department.id}", json={"is_active": False}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is False
    assert body["name"] == "Engineering"
    assert body["description"] == "Builds things"  # omitted field left untouched

    logs = db.scalars(select(AuditLog).where(AuditLog.action == "department.updated").order_by(AuditLog.id)).all()
    assert [log.details for log in logs] == [
        {"changes": {"description": {"from": None, "to": "Builds things"}}},
        {"changes": {"is_active": {"from": True, "to": False}}},
    ]


def test_patch_description_can_be_cleared(client, headers_for, make_department) -> None:
    department = make_department("Engineering")
    headers = headers_for(ADMIN)
    client.patch(f"/api/departments/{department.id}", json={"description": "x"}, headers=headers)
    body = client.patch(f"/api/departments/{department.id}", json={"description": None}, headers=headers).json()
    assert body["description"] is None


def test_patch_rename_conflict_and_self_rename(client, headers_for, make_department) -> None:
    engineering = make_department("Engineering")
    make_department("Sales")
    headers = headers_for(HR)

    conflict = client.patch(f"/api/departments/{engineering.id}", json={"name": "SALES"}, headers=headers)
    assert conflict.status_code == 409

    # Changing only the letter case of its own name is not a conflict.
    own = client.patch(f"/api/departments/{engineering.id}", json={"name": "ENGINEERING"}, headers=headers)
    assert own.status_code == 200
    assert own.json()["name"] == "ENGINEERING"


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"is_active": None}, {"name": " "}, {"id": 5}])
def test_patch_invalid_422(client, headers_for, make_department, payload: dict) -> None:
    department = make_department()
    response = client.patch(f"/api/departments/{department.id}", json=payload, headers=headers_for(ADMIN))
    assert response.status_code == 422


def test_patch_no_changes_writes_no_audit(client, headers_for, make_department, db) -> None:
    department = make_department("Engineering")
    response = client.patch(f"/api/departments/{department.id}", json={"name": "Engineering"}, headers=headers_for(ADMIN))
    assert response.status_code == 200
    assert db.scalar(select(AuditLog).where(AuditLog.action == "department.updated")) is None


def test_patch_404(client, headers_for) -> None:
    assert client.patch("/api/departments/999", json={"is_active": False}, headers=headers_for(ADMIN)).status_code == 404


def test_departments_cannot_be_deleted(client, headers_for, make_department) -> None:
    department = make_department()
    assert client.delete(f"/api/departments/{department.id}", headers=headers_for(ADMIN)).status_code == 405


# --- Department employees ---------------------------------------------------------------------------


def test_department_employees_list_and_status_filter(client, headers_for, make_department, make_employee) -> None:
    engineering = make_department("Engineering")
    sales = make_department("Sales")
    make_employee(engineering, full_name="Zed")
    make_employee(engineering, full_name="Amy", status=EmploymentStatus.ON_LEAVE)
    make_employee(sales)
    headers = headers_for(HR)

    body = client.get(f"/api/departments/{engineering.id}/employees", headers=headers).json()
    assert body["total"] == 2
    assert [e["full_name"] for e in body["items"]] == ["Amy", "Zed"]
    assert body["items"][0]["department"] == {"id": engineering.id, "name": "Engineering"}

    on_leave = client.get(
        f"/api/departments/{engineering.id}/employees", params={"status": "ON_LEAVE"}, headers=headers
    ).json()
    assert [e["full_name"] for e in on_leave["items"]] == ["Amy"]


def test_department_employees_404(client, headers_for) -> None:
    assert client.get("/api/departments/999/employees", headers=headers_for(ADMIN)).status_code == 404


def test_manager_sees_only_own_department_members(
    client, make_user, make_department, make_employee
) -> None:
    from app.core.security import create_access_token
    from tests.conftest import auth_header

    engineering = make_department("Engineering")
    sales = make_department("Sales")
    manager = make_user(email="manager@example.com", role=MANAGER)
    make_employee(engineering, user_id=manager.id)
    headers = auth_header(create_access_token(manager.id).token)

    assert client.get(f"/api/departments/{engineering.id}/employees", headers=headers).status_code == 200
    other = client.get(f"/api/departments/{sales.id}/employees", headers=headers)
    assert other.status_code == 403


def test_manager_without_employee_record_cannot_list_members(client, headers_for, make_department) -> None:
    department = make_department()
    assert client.get(f"/api/departments/{department.id}/employees", headers=headers_for(MANAGER)).status_code == 403


# --- RBAC matrix --------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ALL_ROLES, ids=[r.value for r in ALL_ROLES])
def test_rbac_matrix(client, headers_for, make_department, role: UserRole) -> None:
    department = make_department("Engineering")
    headers = headers_for(role)

    create = client.post("/api/departments", json={"name": "New Dept"}, headers=headers)
    update = client.patch(f"/api/departments/{department.id}", json={"description": "x"}, headers=headers)
    listing = client.get("/api/departments", headers=headers)
    detail = client.get(f"/api/departments/{department.id}", headers=headers)
    members = client.get(f"/api/departments/{department.id}/employees", headers=headers)

    expected_write = 201 if role in WRITERS else 403
    assert create.status_code == expected_write
    assert update.status_code == (200 if role in WRITERS else 403)
    assert listing.status_code == 200
    assert detail.status_code == 200
    # Managers without an employee record in this department get 403 too.
    assert members.status_code == (200 if role in WRITERS else 403)


@pytest.mark.parametrize(
    "method, path",
    [("get", "/api/departments"), ("post", "/api/departments"), ("get", "/api/departments/1"),
     ("patch", "/api/departments/1"), ("get", "/api/departments/1/employees")],
)
def test_requires_authentication(client, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401
