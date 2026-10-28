from datetime import time

import pytest
from sqlalchemy import select

from app.models import AuditLog, UserRole

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
ALL_ROLES = list(UserRole)
WRITERS = {ADMIN, HR}

DAY = {"name": "General", "start_time": "09:00", "end_time": "18:00", "break_minutes": 60}


def test_create_day_shift(client, headers_for, db) -> None:
    response = client.post("/api/shifts", json={**DAY, "name": "  General  Shift "}, headers=headers_for(HR))
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "General Shift"
    assert body["start_time"] == "09:00:00" and body["end_time"] == "18:00:00"
    assert body["is_overnight"] is False
    assert body["duration_minutes"] == 540
    assert body["scheduled_work_minutes"] == 480
    assert body["overtime_threshold_minutes"] == 480
    assert body["is_active"] is True

    log = db.scalar(select(AuditLog).where(AuditLog.action == "shift.created"))
    assert log.details == {"name": "General Shift"}


def test_create_overnight_shift(client, headers_for) -> None:
    body = client.post(
        "/api/shifts",
        json={"name": "Night", "start_time": "22:00", "end_time": "06:00", "break_minutes": 30,
              "overtime_threshold_minutes": 450},
        headers=headers_for(ADMIN),
    ).json()
    assert body["is_overnight"] is True
    assert body["duration_minutes"] == 480
    assert body["scheduled_work_minutes"] == 450


@pytest.mark.parametrize("name", ["general", " GENERAL "])
def test_duplicate_name_409(client, headers_for, make_shift, name: str) -> None:
    make_shift("General")
    response = client.post("/api/shifts", json={**DAY, "name": name}, headers=headers_for(ADMIN))
    assert response.status_code == 409
    assert response.json() == {"detail": "A shift with this name already exists"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_time": "09:00", "end_time": "09:00"},
        {"break_minutes": 540},
        {"break_minutes": 241},
        {"break_minutes": -1},
        {"start_time": "10:00", "end_time": "11:00", "break_minutes": 60},
        {"overtime_threshold_minutes": 0},
        {"overtime_threshold_minutes": 1441},
        {"start_time": "09:00:30"},
        {"start_time": "09:00+05:30"},
        {"start_time": "25:00"},
        {"name": ""},
        {"name": "<b>x</b>"},
        {"work_minutes": 480},
    ],
    ids=["same-times", "break-equals-duration", "break-over-max", "negative-break", "break-fills-shift",
         "zero-threshold", "threshold-over-day", "seconds", "utc-offset", "bad-hour", "empty-name",
         "markup-name", "extra-field"],
)
def test_create_validation_422(client, headers_for, overrides: dict) -> None:
    assert client.post("/api/shifts", json={**DAY, **overrides}, headers=headers_for(ADMIN)).status_code == 422


def test_patch_partial_update_and_audit(client, headers_for, make_shift, db) -> None:
    shift = make_shift("General")
    response = client.patch(f"/api/shifts/{shift.id}", json={"end_time": "18:30"}, headers=headers_for(HR))
    assert response.status_code == 200
    body = response.json()
    assert body["end_time"] == "18:30:00"
    assert body["start_time"] == "09:00:00" and body["break_minutes"] == 60  # untouched

    log = db.scalar(select(AuditLog).where(AuditLog.action == "shift.updated"))
    assert log.details == {"changes": {"end_time": {"from": "18:00:00", "to": "18:30:00"}}}


@pytest.mark.parametrize(
    "payload, detail",
    [
        ({"end_time": "09:00"}, "start_time and end_time must differ"),
        ({"end_time": "09:30"}, "break_minutes must be shorter than the shift"),  # 30-min shift, 60-min break
        ({"start_time": "17:30"}, "break_minutes must be shorter than the shift"),
    ],
)
def test_patch_validates_merged_window(client, headers_for, make_shift, payload, detail) -> None:
    """Each field is valid alone; the conflict only appears when merged with the stored values."""
    shift = make_shift("General")
    response = client.patch(f"/api/shifts/{shift.id}", json=payload, headers=headers_for(ADMIN))
    assert response.status_code == 422
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"start_time": None}, {"is_active": False}])
def test_patch_invalid_body_422(client, headers_for, make_shift, payload: dict) -> None:
    shift = make_shift()
    assert client.patch(f"/api/shifts/{shift.id}", json=payload, headers=headers_for(ADMIN)).status_code == 422


def test_patch_rename_conflict(client, headers_for, make_shift) -> None:
    make_shift("General")
    night = make_shift("Night", start=time(22, 0), end=time(6, 0), break_minutes=30)
    assert client.patch(f"/api/shifts/{night.id}", json={"name": "GENERAL"}, headers=headers_for(ADMIN)).status_code == 409


def test_deactivate_and_visibility(client, headers_for, make_shift, db) -> None:
    active = make_shift("General")
    retired = make_shift("Retired", start=time(7, 0), end=time(15, 0))
    hr = headers_for(HR)
    employee = headers_for(EMPLOYEE)

    response = client.patch(
        f"/api/shifts/{retired.id}/status", json={"is_active": False, "reason": "Replaced"}, headers=hr
    )
    assert response.status_code == 200 and response.json()["is_active"] is False
    log = db.scalar(select(AuditLog).where(AuditLog.action == "shift.status_changed"))
    assert log.details == {"changes": {"is_active": {"from": True, "to": False}}, "reason": "Replaced"}

    # Employees only ever see active shifts, even if they ask for inactive ones.
    assert [s["name"] for s in client.get("/api/shifts", headers=employee).json()["items"]] == ["General"]
    assert client.get("/api/shifts", params={"is_active": "false"}, headers=employee).json()["total"] == 1
    assert client.get(f"/api/shifts/{retired.id}", headers=employee).status_code == 404
    assert client.get(f"/api/shifts/{active.id}", headers=employee).status_code == 200

    # HR sees everything and can filter.
    assert client.get("/api/shifts", headers=hr).json()["total"] == 2
    assert [s["name"] for s in client.get("/api/shifts", params={"is_active": "false"}, headers=hr).json()["items"]] == ["Retired"]
    assert client.get(f"/api/shifts/{retired.id}", headers=hr).status_code == 200


def test_list_order_and_pagination(client, headers_for, make_shift) -> None:
    make_shift("Night", start=time(22, 0), end=time(6, 0), break_minutes=30)
    make_shift("Morning", start=time(6, 0), end=time(14, 0), break_minutes=30)
    make_shift("General")
    body = client.get("/api/shifts", params={"page_size": 2}, headers=headers_for(MANAGER)).json()
    assert [s["name"] for s in body["items"]] == ["Morning", "General"]
    assert (body["total"], body["pages"]) == (3, 2)


def test_not_found_and_no_delete(client, headers_for, make_shift) -> None:
    shift = make_shift()
    headers = headers_for(ADMIN)
    assert client.get("/api/shifts/999", headers=headers).status_code == 404
    assert client.patch("/api/shifts/999", json={"name": "X1"}, headers=headers).status_code == 404
    assert client.delete(f"/api/shifts/{shift.id}", headers=headers).status_code == 405


@pytest.mark.parametrize("role", ALL_ROLES, ids=[r.value for r in ALL_ROLES])
def test_rbac(client, headers_for, make_shift, role: UserRole) -> None:
    shift = make_shift()
    headers = headers_for(role)
    allowed = role in WRITERS
    assert client.post("/api/shifts", json={**DAY, "name": "Other"}, headers=headers).status_code == (201 if allowed else 403)
    assert client.patch(f"/api/shifts/{shift.id}", json={"break_minutes": 45}, headers=headers).status_code == (200 if allowed else 403)
    assert client.patch(f"/api/shifts/{shift.id}/status", json={"is_active": True}, headers=headers).status_code == (200 if allowed else 403)
    assert client.get("/api/shifts", headers=headers).status_code == 200


def test_requires_authentication(client) -> None:
    assert client.get("/api/shifts").status_code == 401
    assert client.post("/api/shifts", json=DAY).status_code == 401
