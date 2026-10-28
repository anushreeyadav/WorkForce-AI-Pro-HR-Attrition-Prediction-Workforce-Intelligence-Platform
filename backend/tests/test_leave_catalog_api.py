"""Leave types and policies."""

from datetime import date

import pytest
from sqlalchemy import select

from app.models import AuditLog, Department, UserRole

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
ALL_ROLES = list(UserRole)
WRITERS = {ADMIN, HR}


# --- Leave types --------------------------------------------------------------------------------


def test_create_leave_type(client, headers_for, db) -> None:
    response = client.post(
        "/api/leave/types",
        json={"name": " Annual  Leave ", "code": "annual", "description": "Paid time off"},
        headers=headers_for(HR),
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["code"], body["is_paid"], body["is_active"]) == ("Annual Leave", "ANNUAL", True, True)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave_type.created"))
    assert log.details == {"code": "ANNUAL", "name": "Annual Leave"}


@pytest.mark.parametrize(
    "payload, detail",
    [
        ({"name": "annual leave", "code": "OTHER"}, "A leave type with this name already exists"),
        ({"name": "Something Else", "code": "Annual"}, "A leave type with this code already exists"),
    ],
)
def test_duplicate_name_or_code_409(client, headers_for, make_leave_type, payload, detail) -> None:
    make_leave_type("ANNUAL", "Annual Leave")
    response = client.post("/api/leave/types", json=payload, headers=headers_for(ADMIN))
    assert response.status_code == 409
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Sick", "code": "1SICK"},
        {"name": "Sick", "code": "S"},
        {"name": "Sick", "code": "SICK LEAVE"},
        {"name": "", "code": "SICK"},
        {"name": "Sick", "code": "SICK", "is_active": False, "extra": 1},
        {"name": "Sick"},
    ],
    ids=["code-starts-digit", "code-short", "code-space", "empty-name", "extra-field", "missing-code"],
)
def test_leave_type_validation_422(client, headers_for, payload) -> None:
    assert client.post("/api/leave/types", json=payload, headers=headers_for(ADMIN)).status_code == 422


def test_update_leave_type_code_is_immutable(client, headers_for, make_leave_type, db) -> None:
    leave_type = make_leave_type("SICK", "Sick Leave")
    headers = headers_for(HR)
    assert client.patch(f"/api/leave/types/{leave_type.id}", json={"code": "ILL"}, headers=headers).status_code == 422

    response = client.patch(f"/api/leave/types/{leave_type.id}", json={"is_paid": False, "name": "Sickness"}, headers=headers)
    assert response.status_code == 200
    assert (response.json()["is_paid"], response.json()["name"], response.json()["code"]) == (False, "Sickness", "SICK")
    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave_type.updated"))
    assert log.details["changes"]["is_paid"] == {"from": True, "to": False}


def test_type_visibility(client, headers_for, make_leave_type) -> None:
    make_leave_type("ANNUAL")
    make_leave_type("OLD", "Old Scheme", is_active=False)
    employee = headers_for(EMPLOYEE)
    hr = headers_for(HR)
    assert [t["code"] for t in client.get("/api/leave/types", headers=employee).json()["items"]] == ["ANNUAL"]
    assert client.get("/api/leave/types", params={"is_active": "false"}, headers=employee).json()["total"] == 1
    assert client.get("/api/leave/types", headers=hr).json()["total"] == 2
    assert [t["code"] for t in client.get("/api/leave/types", params={"is_active": "false"}, headers=hr).json()["items"]] == ["OLD"]


def test_no_delete_endpoints(client, headers_for, make_leave_type, make_policy) -> None:
    leave_type = make_leave_type()
    policy = make_policy(leave_type)
    headers = headers_for(ADMIN)
    assert client.delete(f"/api/leave/types/{leave_type.id}", headers=headers).status_code == 405
    assert client.delete(f"/api/leave/policies/{policy.id}", headers=headers).status_code == 405


# --- Policies -----------------------------------------------------------------------------------------


def policy_body(leave_type_id: int, **overrides) -> dict:
    return {"leave_type_id": leave_type_id, "annual_allowance_days": 12, "carry_forward_days": 5,
            "max_consecutive_days": 10, "effective_from": "2026-01-01", **overrides}


def test_create_policy_and_audit(client, headers_for, make_leave_type, db) -> None:
    leave_type = make_leave_type()
    response = client.post("/api/leave/policies", json=policy_body(leave_type.id), headers=headers_for(HR))
    assert response.status_code == 201
    body = response.json()
    assert body["leave_type"] == {"id": leave_type.id, "code": "ANNUAL", "name": "Annual"}
    assert (body["department_id"], body["annual_allowance_days"], body["count_weekends"]) == (None, 12, False)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave_policy.created"))
    assert log.details["annual_allowance_days"] == 12 and log.details["effective_from"] == "2026-01-01"


@pytest.mark.parametrize(
    "existing, new, expected",
    [
        ({"effective_from": "2026-01-01"}, {"effective_from": "2026-06-01"}, 409),  # open-ended covers it
        ({"effective_from": "2026-01-01", "effective_to": "2026-12-31"}, {"effective_from": "2027-01-01"}, 201),
        ({"effective_from": "2026-06-01"}, {"effective_from": "2026-01-01", "effective_to": "2026-05-31"}, 201),
        ({"effective_from": "2026-06-01"}, {"effective_from": "2026-01-01", "effective_to": "2026-06-01"}, 409),
        ({"effective_from": "2026-01-01", "is_active": False}, {"effective_from": "2026-01-01"}, 201),
        ({"effective_from": "2026-01-01"}, {"effective_from": "2026-01-01", "is_active": False}, 201),
    ],
    ids=["open-ended-overlap", "after-closed", "before-open", "touching-day", "existing-inactive", "new-inactive"],
)
def test_policy_overlap_rules(client, headers_for, make_leave_type, existing, new, expected) -> None:
    leave_type = make_leave_type()
    headers = headers_for(ADMIN)
    assert client.post("/api/leave/policies", json=policy_body(leave_type.id, **existing), headers=headers).status_code == 201
    response = client.post("/api/leave/policies", json=policy_body(leave_type.id, **new), headers=headers)
    assert response.status_code == expected


def test_department_and_org_policies_can_coexist(client, headers_for, make_leave_type, make_department) -> None:
    leave_type = make_leave_type()
    engineering = make_department("Engineering")
    headers = headers_for(ADMIN)
    assert client.post("/api/leave/policies", json=policy_body(leave_type.id), headers=headers).status_code == 201
    dept = client.post("/api/leave/policies", json=policy_body(leave_type.id, department_id=engineering.id), headers=headers)
    assert dept.status_code == 201
    again = client.post("/api/leave/policies", json=policy_body(leave_type.id, department_id=engineering.id), headers=headers)
    assert again.status_code == 409


@pytest.mark.parametrize(
    "overrides",
    [
        {"annual_allowance_days": -1},
        {"annual_allowance_days": 367},
        {"carry_forward_days": -1},
        {"max_consecutive_days": 0},
        {"effective_from": "2026-06-01", "effective_to": "2026-05-31"},
        {"effective_from": "not-a-date"},
        {"employee_id": 3},
    ],
    ids=["negative-allowance", "allowance-too-big", "negative-carry", "zero-consecutive", "inverted-range",
         "bad-date", "extra-field"],
)
def test_policy_validation_422(client, headers_for, make_leave_type, overrides) -> None:
    leave_type = make_leave_type()
    assert client.post("/api/leave/policies", json=policy_body(leave_type.id, **overrides),
                       headers=headers_for(ADMIN)).status_code == 422


def test_policy_references_validated(client, headers_for, make_leave_type) -> None:
    leave_type = make_leave_type()
    headers = headers_for(ADMIN)
    assert client.post("/api/leave/policies", json=policy_body(999), headers=headers).json() == {"detail": "Leave type does not exist"}
    assert client.post("/api/leave/policies", json=policy_body(leave_type.id, department_id=999),
                       headers=headers).json() == {"detail": "Department does not exist"}


def test_policy_patch_merges_and_validates(client, headers_for, make_leave_type, make_policy, db) -> None:
    leave_type = make_leave_type()
    first = make_policy(leave_type, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30))
    second = make_policy(leave_type, effective_from=date(2026, 7, 1))
    headers = headers_for(HR)

    inverted = client.patch(f"/api/leave/policies/{first.id}", json={"effective_to": "2025-12-31"}, headers=headers)
    assert inverted.status_code == 422
    assert inverted.json() == {"detail": "effective_to must be on or after effective_from"}

    overlap = client.patch(f"/api/leave/policies/{first.id}", json={"effective_to": None}, headers=headers)
    assert overlap.status_code == 409

    ok = client.patch(f"/api/leave/policies/{second.id}", json={"annual_allowance_days": 15, "max_consecutive_days": None},
                      headers=headers)
    assert ok.status_code == 200
    assert (ok.json()["annual_allowance_days"], ok.json()["max_consecutive_days"]) == (15, None)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave_policy.updated"))
    assert log.details["changes"]["annual_allowance_days"] == {"from": 10, "to": 15}

    assert client.patch(f"/api/leave/policies/{second.id}", json={"leave_type_id": 5}, headers=headers).status_code == 422
    assert client.patch("/api/leave/policies/999", json={"annual_allowance_days": 1}, headers=headers).status_code == 404


def test_policy_visibility_for_employees(client, db, staff, headers_for, make_leave_type, make_policy, make_department) -> None:
    annual = make_leave_type("ANNUAL")
    retired_type = make_leave_type("OLD", is_active=False)
    engineer_record, engineer = staff(department="Engineering")
    engineering = db.get(Department, engineer_record.department_id)

    sales = make_department("Sales")
    org = make_policy(annual)
    mine = make_policy(annual, department=engineering)
    make_policy(annual, department=sales)
    make_policy(annual, is_active=False, effective_from=date(2030, 1, 1))
    make_policy(retired_type)

    visible = {p["id"] for p in client.get("/api/leave/policies", headers=engineer).json()["items"]}
    assert visible == {org.id, mine.id}
    assert client.get("/api/leave/policies", headers=headers_for(HR)).json()["total"] == 5
    # No linked employee: organization-wide active policies only.
    assert {p["id"] for p in client.get("/api/leave/policies", headers=headers_for(MANAGER)).json()["items"]} == {org.id}


@pytest.mark.parametrize("role", ALL_ROLES, ids=[r.value for r in ALL_ROLES])
def test_catalog_rbac(client, headers_for, make_leave_type, role) -> None:
    leave_type = make_leave_type()
    headers = headers_for(role)
    allowed = role in WRITERS
    assert client.post("/api/leave/types", json={"name": "Sick", "code": "SICK"}, headers=headers).status_code == (201 if allowed else 403)
    assert client.patch(f"/api/leave/types/{leave_type.id}", json={"is_paid": False}, headers=headers).status_code == (200 if allowed else 403)
    assert client.post("/api/leave/policies", json=policy_body(leave_type.id), headers=headers).status_code == (201 if allowed else 403)
    assert client.get("/api/leave/types", headers=headers).status_code == 200
    assert client.get("/api/leave/policies", headers=headers).status_code == 200


def test_catalog_requires_authentication(client) -> None:
    assert client.get("/api/leave/types").status_code == 401
    assert client.get("/api/leave/policies").status_code == 401
