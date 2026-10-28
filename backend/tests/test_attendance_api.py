"""Attendance API. Server time comes from the `clock` fixture; ORG_TIMEZONE is Asia/Kolkata (UTC+05:30)."""

from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Attendance, AttendanceStatus, AuditLog, EmploymentStatus, UserRole
from tests.conftest import constraint_violation, local

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE

READ_FIELDS = {
    "id", "employee", "shift", "work_date", "check_in", "check_out", "break_minutes", "work_minutes",
    "overtime_minutes", "status", "is_open", "note", "created_at", "updated_at",
}


def check_in(client, headers, clock, at, **body):
    clock.set(at)
    return client.post("/api/attendance/check-in", json=body, headers=headers)


def check_out(client, headers, clock, at, **body):
    clock.set(at)
    return client.post("/api/attendance/check-out", json=body, headers=headers)


def work_day(client, headers, clock, day: int, start=(9, 0), end=(18, 0), **check_in_body):
    """Check in and out on 2026-03-<day> (local time). Returns the check-out response JSON."""
    assert check_in(client, headers, clock, local(2026, 3, day, *start), **check_in_body).status_code == 201
    response = check_out(client, headers, clock, local(2026, 3, day, *end))
    assert response.status_code == 200, response.text
    return response.json()


# --- Check-in / check-out ----------------------------------------------------------------------------


def test_check_in_and_out_with_shift(client, staff, make_shift, clock) -> None:
    employee, headers = staff()
    shift = make_shift()

    response = check_in(client, headers, clock, local(2026, 3, 10, 8, 55), shift_id=shift.id, note="On site")
    assert response.status_code == 201
    body = response.json()
    assert set(body) == READ_FIELDS
    assert body["employee"] == {
        "id": employee.id, "employee_code": employee.employee_code, "full_name": employee.full_name,
        "department_id": employee.department_id,
    }
    assert body["shift"] == {"id": shift.id, "name": "General"}
    assert body["work_date"] == "2026-03-10"
    assert body["check_in"].startswith("2026-03-10T03:25:00")  # 08:55 IST in UTC
    assert body["status"] == "PRESENT" and body["is_open"] is True
    assert body["work_minutes"] is None and body["note"] == "On site"

    out = check_out(client, headers, clock, local(2026, 3, 10, 18, 40)).json()
    assert out["is_open"] is False
    assert out["break_minutes"] == 60  # shift default
    assert out["work_minutes"] == 9 * 60 + 45 - 60  # 525
    assert out["overtime_minutes"] == 45
    assert out["status"] == "PRESENT"
    assert out["note"] == "On site"


def test_check_out_with_recorded_break_and_note(client, staff, clock) -> None:
    _, headers = staff()
    check_in(client, headers, clock, local(2026, 3, 10, 9, 0))
    out = check_out(client, headers, clock, local(2026, 3, 10, 17, 30), break_minutes=30, note="Left early").json()
    assert (out["break_minutes"], out["work_minutes"], out["overtime_minutes"]) == (30, 480, 0)
    assert out["note"] == "Left early"


def test_late_arrival_and_half_day(client, staff, make_shift, clock) -> None:
    _, headers = staff()
    shift = make_shift()
    late = check_in(client, headers, clock, local(2026, 3, 10, 9, 20), shift_id=shift.id).json()
    assert late["status"] == "LATE"
    assert check_out(client, headers, clock, local(2026, 3, 10, 18, 0)).json()["status"] == "LATE"

    check_in(client, headers, clock, local(2026, 3, 11, 9, 0), shift_id=shift.id)
    half = check_out(client, headers, clock, local(2026, 3, 11, 13, 0)).json()  # 240 - 60 = 180 < 240
    assert half["status"] == "HALF_DAY"


def test_overnight_shift_after_midnight_check_in(client, staff, make_shift, clock) -> None:
    _, headers = staff()
    night = make_shift("Night", start=time(22, 0), end=time(6, 0), break_minutes=30, overtime_threshold_minutes=450)

    body = check_in(client, headers, clock, local(2026, 3, 11, 0, 30), shift_id=night.id).json()
    assert body["work_date"] == "2026-03-10"  # belongs to the shift that started on the 10th
    assert body["status"] == "LATE"

    out = check_out(client, headers, clock, local(2026, 3, 11, 7, 0)).json()
    assert out["work_date"] == "2026-03-10"
    assert out["work_minutes"] == 6 * 60 + 30 - 30  # 360
    assert out["overtime_minutes"] == 0


def test_timezone_boundary_without_shift(client, staff, clock) -> None:
    _, headers = staff()
    # 00:15 IST on the 11th is still the 10th in UTC; the work date must follow the org time zone.
    body = check_in(client, headers, clock, local(2026, 3, 11, 0, 15)).json()
    assert body["work_date"] == "2026-03-11"
    assert body["check_in"].startswith("2026-03-10T18:45:00")


def test_duplicate_check_in_rejected(client, staff, clock) -> None:
    _, headers = staff()
    assert check_in(client, headers, clock, local(2026, 3, 10, 9, 0)).status_code == 201
    again = check_in(client, headers, clock, local(2026, 3, 10, 9, 5))
    assert again.status_code == 409
    assert "already checked in" in again.json()["detail"]


def test_repeat_check_in_after_check_out_rejected(client, staff, clock) -> None:
    _, headers = staff()
    work_day(client, headers, clock, 10)
    again = check_in(client, headers, clock, local(2026, 3, 10, 19, 0))
    assert again.status_code == 409
    assert "already recorded" in again.json()["detail"]
    # The next day is fine.
    assert check_in(client, headers, clock, local(2026, 3, 11, 9, 0)).status_code == 201


def test_check_out_without_check_in(client, staff, clock) -> None:
    _, headers = staff()
    response = check_out(client, headers, clock, local(2026, 3, 10, 18, 0))
    assert response.status_code == 409
    assert response.json() == {"detail": "No active check-in to check out from"}


def test_check_out_before_check_in_rejected(client, staff, clock, db) -> None:
    _, headers = staff()
    check_in(client, headers, clock, local(2026, 3, 10, 9, 0))
    for earlier in (local(2026, 3, 10, 8, 59), local(2026, 3, 10, 9, 0)):
        response = check_out(client, headers, clock, earlier)
        assert response.status_code == 409
        assert response.json() == {"detail": "Check-out time cannot be before check-in"}
    assert db.scalar(select(Attendance)).check_out is None


def test_stale_open_check_in(client, staff, clock) -> None:
    _, headers = staff()
    check_in(client, headers, clock, local(2026, 3, 10, 9, 0))
    response = check_out(client, headers, clock, local(2026, 3, 11, 9, 1))
    assert response.status_code == 409
    assert "more than 24 hours" in response.json()["detail"]
    # The open record also blocks a new check-in until HR corrects it.
    assert check_in(client, headers, clock, local(2026, 3, 11, 9, 2)).status_code == 409


def test_break_longer_than_session_rejected(client, staff, clock) -> None:
    _, headers = staff()
    check_in(client, headers, clock, local(2026, 3, 10, 9, 0))
    response = check_out(client, headers, clock, local(2026, 3, 10, 9, 30), break_minutes=31)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/attendance/check-in", {"employee_id": 1}),
        ("/api/attendance/check-in", {"check_in": "2026-03-10T09:00:00Z"}),
        ("/api/attendance/check-in", {"status": "PRESENT"}),
        ("/api/attendance/check-out", {"work_minutes": 480}),
        ("/api/attendance/check-out", {"overtime_minutes": 60}),
        ("/api/attendance/check-out", {"check_out": "2026-03-10T18:00:00Z"}),
        ("/api/attendance/check-out", {"break_minutes": -5}),
        ("/api/attendance/check-in", {"note": "n" * 501}),
    ],
)
def test_clients_cannot_submit_identity_times_or_calculations(client, staff, clock, path, body) -> None:
    _, headers = staff()
    clock.set(local(2026, 3, 10, 9, 0))
    assert client.post(path, json=body, headers=headers).status_code == 422


def test_check_in_with_missing_or_inactive_shift(client, staff, make_shift, clock) -> None:
    _, headers = staff()
    retired = make_shift("Retired", is_active=False)
    assert check_in(client, headers, clock, local(2026, 3, 10, 9, 0), shift_id=999).json() == {"detail": "Shift does not exist"}
    assert check_in(client, headers, clock, local(2026, 3, 10, 9, 0), shift_id=retired.id).json() == {"detail": "Shift is inactive"}


@pytest.mark.parametrize("status", [EmploymentStatus.INACTIVE, EmploymentStatus.ON_LEAVE])
def test_non_active_employee_cannot_check_in(client, staff, clock, status) -> None:
    _, headers = staff(status=status)
    assert check_in(client, headers, clock, local(2026, 3, 10, 9, 0)).status_code == 409


@pytest.mark.parametrize(
    "method, path",
    [("post", "/api/attendance/check-in"), ("post", "/api/attendance/check-out"),
     ("get", "/api/attendance/me"), ("get", "/api/attendance/me/summary")],
)
def test_no_linked_employee_404(client, headers_for, clock, method, path) -> None:
    headers = headers_for(HR)  # an HR login without an employee record
    kwargs = {"json": {}} if method == "post" else {}
    response = getattr(client, method)(path, headers=headers, **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "No employee profile is linked to your account"}


# --- Self-service history and isolation -------------------------------------------------------------


def test_me_lists_only_own_records(client, staff, clock) -> None:
    _, alice = staff()
    _, bob = staff()
    for day in (10, 11, 12):
        work_day(client, alice, clock, day)
    work_day(client, bob, clock, 10)

    body = client.get("/api/attendance/me", headers=alice).json()
    assert body["total"] == 3
    assert [r["work_date"] for r in body["items"]] == ["2026-03-12", "2026-03-11", "2026-03-10"]

    ranged = client.get("/api/attendance/me", params={"date_from": "2026-03-11", "date_to": "2026-03-11"}, headers=alice).json()
    assert [r["work_date"] for r in ranged["items"]] == ["2026-03-11"]
    # An employee_id parameter is not accepted, so nobody can ask for someone else's history.
    assert client.get("/api/attendance/me", params={"employee_id": 1}, headers=alice).status_code == 422


def test_employee_cannot_read_others_records(client, staff, clock) -> None:
    _, alice = staff()
    _, bob = staff()
    bob_record = work_day(client, bob, clock, 10)
    alice_record = work_day(client, alice, clock, 10)

    assert client.get(f"/api/attendance/{alice_record['id']}", headers=alice).status_code == 200
    hidden = client.get(f"/api/attendance/{bob_record['id']}", headers=alice)
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "Attendance record not found"}
    assert client.get("/api/attendance", headers=alice).status_code == 403
    assert client.get("/api/attendance/summary", headers=alice).status_code == 403


def test_my_summary(client, staff, make_shift, clock) -> None:
    _, headers = staff()
    shift = make_shift()
    work_day(client, headers, clock, 2, shift_id=shift.id)                         # PRESENT, 480
    work_day(client, headers, clock, 3, start=(9, 30), end=(19, 0), shift_id=shift.id)  # LATE, 510 (30 OT)
    work_day(client, headers, clock, 4, end=(12, 0), shift_id=shift.id)            # HALF_DAY, 120
    check_in(client, headers, clock, local(2026, 3, 5, 9, 0), shift_id=shift.id)   # open

    clock.set(local(2026, 3, 5, 12, 0))
    body = client.get("/api/attendance/me/summary", headers=headers).json()
    assert body == {
        "date_from": "2026-03-01", "date_to": "2026-03-05",  # default: month to date (org time)
        "records": 4, "attended_days": 4, "present_days": 2, "late_days": 1, "half_days": 1,
        "absent_days": 0, "on_leave_days": 0, "open_records": 1,
        "work_minutes": 480 + 510 + 120, "overtime_minutes": 30,
        "attendance_percentage": 100.0,
    }

    empty = client.get("/api/attendance/me/summary", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}, headers=headers).json()
    assert empty["records"] == 0
    assert empty["attendance_percentage"] is None  # missing days are not treated as absences


@pytest.mark.parametrize(
    "params",
    [{"date_from": "2026-03-10", "date_to": "2026-03-01"}, {"date_from": "2025-01-01", "date_to": "2026-03-01"},
     {"date_from": "not-a-date"}, {"foo": "bar"}],
)
def test_summary_param_validation(client, staff, clock, params) -> None:
    _, headers = staff()
    assert client.get("/api/attendance/me/summary", params=params, headers=headers).status_code == 422


# --- Reports and scope --------------------------------------------------------------------------------


@pytest.fixture
def org(client, staff, make_shift, clock):
    """Engineering: manager + 2 staff. Sales: 1 staff. Records on 10th and 11th March."""
    general = make_shift()
    night = make_shift("Night", start=time(22, 0), end=time(6, 0), break_minutes=30)
    manager, manager_h = staff(MANAGER, department="Engineering")
    eng1, eng1_h = staff(department="Engineering")
    eng2, eng2_h = staff(department="Engineering")
    sales, sales_h = staff(department="Sales")

    work_day(client, eng1_h, clock, 10, shift_id=general.id)
    work_day(client, eng1_h, clock, 11, start=(9, 40), shift_id=general.id)  # LATE
    work_day(client, eng2_h, clock, 10, shift_id=general.id)
    work_day(client, manager_h, clock, 10)
    work_day(client, sales_h, clock, 10, start=(21, 55), end=(23, 30), shift_id=night.id)  # HALF_DAY
    return {"general": general, "night": night, "manager": manager, "manager_h": manager_h,
            "eng1": eng1, "eng2": eng2, "sales": sales}


def codes(response) -> list[str]:
    return [r["employee"]["employee_code"] for r in response.json()["items"]]


def test_report_hr_filters(client, headers_for, org) -> None:
    hr = headers_for(HR)
    everything = client.get("/api/attendance", headers=hr).json()
    assert everything["total"] == 5
    # Stable ordering: newest work date first, then newest record.
    assert [r["work_date"] for r in everything["items"]] == ["2026-03-11"] + ["2026-03-10"] * 4

    def total(**params):
        return client.get("/api/attendance", params=params, headers=hr).json()["total"]

    assert total(employee_id=org["eng1"].id) == 2
    assert total(department_id=org["sales"].department_id) == 1
    assert total(shift_id=org["night"].id) == 1
    assert total(status="LATE") == 1
    assert total(status="HALF_DAY") == 1
    assert total(date_from="2026-03-11") == 1
    assert total(date_from="2026-03-10", date_to="2026-03-10") == 4

    page = client.get("/api/attendance", params={"page": 2, "page_size": 2}, headers=hr).json()
    assert (page["page"], page["pages"], len(page["items"])) == (2, 3, 2)


def test_report_manager_scope(client, org) -> None:
    headers = org["manager_h"]
    response = client.get("/api/attendance", headers=headers)
    assert response.status_code == 200
    assert sorted(codes(response)) == sorted(
        [org["eng1"].employee_code] * 2 + [org["eng2"].employee_code, org["manager"].employee_code]
    )
    # Filtering to another department or employee yields nothing rather than leaking.
    assert client.get("/api/attendance", params={"department_id": org["sales"].department_id}, headers=headers).json()["total"] == 0
    assert client.get("/api/attendance", params={"employee_id": org["sales"].id}, headers=headers).json()["total"] == 0

    summary = client.get("/api/attendance/summary", headers=headers).json()
    assert summary["records"] == 4


def test_report_summary_for_hr(client, headers_for, org) -> None:
    body = client.get("/api/attendance/summary", params={"date_from": "2026-03-10", "date_to": "2026-03-11"},
                      headers=headers_for(HR)).json()
    assert body["records"] == 5
    assert (body["present_days"], body["late_days"], body["half_days"]) == (3, 1, 1)
    assert body["attendance_percentage"] == 100.0


@pytest.mark.parametrize(
    "params",
    [{"date_from": "2026-03-11", "date_to": "2026-03-10"}, {"page_size": 101}, {"status": "MISSING"},
     {"employee_id": 0}, {"sort": "name"}],
)
def test_report_param_validation(client, headers_for, params) -> None:
    assert client.get("/api/attendance", params=params, headers=headers_for(ADMIN)).status_code == 422


def test_detail_scope(client, headers_for, org) -> None:
    hr = headers_for(HR)
    sales_id = client.get("/api/attendance", params={"employee_id": org["sales"].id}, headers=hr).json()["items"][0]["id"]
    eng_id = client.get("/api/attendance", params={"employee_id": org["eng2"].id}, headers=hr).json()["items"][0]["id"]
    assert client.get(f"/api/attendance/{sales_id}", headers=hr).status_code == 200
    assert client.get(f"/api/attendance/{eng_id}", headers=org["manager_h"]).status_code == 200
    assert client.get(f"/api/attendance/{sales_id}", headers=org["manager_h"]).status_code == 404
    assert client.get("/api/attendance/99999", headers=hr).status_code == 404


def test_report_avoids_n_plus_one(client, headers_for, staff, clock, count_queries) -> None:
    hr = headers_for(HR)
    _, first = staff()
    work_day(client, first, clock, 10)
    before = len(count_queries())
    client.get("/api/attendance", headers=hr)
    one = len(count_queries()) - before

    for _ in range(6):
        _, headers = staff(department=f"Dept {_}")
        work_day(client, headers, clock, 10)
    before = len(count_queries())
    body = client.get("/api/attendance", headers=hr).json()
    many = len(count_queries()) - before

    assert body["total"] == 7
    assert many == one


# --- Corrections ------------------------------------------------------------------------------------


@pytest.fixture
def record(client, staff, make_shift, clock):
    """A closed PRESENT record for an Engineering employee on 2026-03-10 (09:00-18:00 local, General shift)."""
    shift = make_shift()
    employee, headers = staff()
    body = work_day(client, headers, clock, 10, shift_id=shift.id)
    return {"id": body["id"], "employee": employee, "headers": headers, "shift": shift}


def correct(client, headers, record_id, **body):
    return client.patch(f"/api/attendance/{record_id}/correction", json=body, headers=headers)


def test_correction_recomputes_and_audits(client, headers_for, record, db) -> None:
    hr = headers_for(HR)
    response = correct(client, hr, record["id"], check_out=local(2026, 3, 10, 20, 0).isoformat(),
                       reason="Badge reader outage")
    assert response.status_code == 200
    body = response.json()
    assert body["work_minutes"] == 600 and body["overtime_minutes"] == 120
    assert body["status"] == "PRESENT"

    log = db.scalar(select(AuditLog).where(AuditLog.action == "attendance.corrected"))
    assert log.entity_id == str(record["id"])
    assert log.details["reason"] == "Badge reader outage"
    assert log.details["employee_id"] == record["employee"].id
    assert set(log.details["changes"]) == {"check_out", "work_minutes", "overtime_minutes"}
    assert log.details["changes"]["work_minutes"] == {"from": 480, "to": 600}


def test_correction_check_in_recomputes_status(client, headers_for, record) -> None:
    body = correct(client, headers_for(ADMIN), record["id"],
                   check_in=local(2026, 3, 10, 10, 0).isoformat(), reason="Arrived late, confirmed").json()
    assert body["status"] == "LATE"
    assert body["work_minutes"] == 480 - 60


def test_correction_accepts_offset_timestamps(client, headers_for, record) -> None:
    # 20:00 IST expressed with an explicit +05:30 offset is stored as 14:30 UTC.
    body = correct(client, headers_for(HR), record["id"], check_out="2026-03-10T20:00:00+05:30", reason="Fix time").json()
    assert body["check_out"].startswith("2026-03-10T14:30:00")


def test_correction_status_override_and_shift_removal(client, headers_for, record) -> None:
    body = correct(client, headers_for(HR), record["id"], status="ON_LEAVE", shift_id=None,
                   reason="Approved leave recorded late").json()
    assert body["status"] == "ON_LEAVE"
    assert body["shift"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"check_out": "2026-03-10T20:00:00Z"},                                   # no reason
        {"check_out": "2026-03-10T20:00:00Z", "reason": "ok"},                    # reason too short
        {"check_out": "2026-03-10T20:00:00", "reason": "Naive timestamp"},        # no offset
        {"check_out": "2026-03-10T02:00:00Z", "reason": "Before check-in"},      # 07:30 IST < 09:00
        {"check_out": "2026-03-12T05:00:00Z", "reason": "Longer than a day"},
        {"break_minutes": 600, "reason": "Break too long"},
        {"work_minutes": 600, "reason": "Direct calculated value"},
        {"overtime_minutes": 5, "reason": "Direct calculated value"},
        {"reason": "Nothing to change"},
        {"check_in": None, "reason": "Null check-in"},
        {"status": "UNKNOWN", "reason": "Bad status"},
    ],
    ids=["no-reason", "short-reason", "naive", "before-check-in", "over-24h", "break-too-long",
         "work-minutes", "overtime", "no-changes", "null-check-in", "bad-status"],
)
def test_correction_validation(client, headers_for, record, db, body) -> None:
    response = correct(client, headers_for(HR), record["id"], **body)
    assert response.status_code == 422
    assert db.scalar(select(AuditLog).where(AuditLog.action == "attendance.corrected")) is None


def test_correction_date_collision_409(client, headers_for, record, clock) -> None:
    work_day(client, record["headers"], clock, 11)
    response = correct(client, headers_for(HR), record["id"],
                       check_in=local(2026, 3, 11, 9, 0).isoformat(),
                       check_out=local(2026, 3, 11, 18, 0).isoformat(), reason="Moved to wrong day")
    assert response.status_code == 409


@pytest.mark.parametrize("role", [MANAGER, EMPLOYEE])
def test_correction_forbidden_roles(client, headers_for, record, role) -> None:
    response = correct(client, headers_for(role), record["id"], status="ABSENT", reason="Not allowed")
    assert response.status_code == 403


def test_cannot_correct_own_attendance(client, staff, clock) -> None:
    _, hr_headers = staff(HR)
    own = work_day(client, hr_headers, clock, 10)
    response = correct(client, hr_headers, own["id"], status="ABSENT", reason="Self correction")
    assert response.status_code == 403
    assert response.json() == {"detail": "You cannot correct your own attendance"}


def test_correction_404(client, headers_for) -> None:
    assert correct(client, headers_for(HR), 999, status="ABSENT", reason="Missing record").status_code == 404


def test_absent_record_affects_percentage(client, headers_for, record, staff, clock) -> None:
    hr = headers_for(HR)
    work_day(client, record["headers"], clock, 11)
    correct(client, hr, record["id"], status="ABSENT", reason="No-show confirmed by manager")

    body = client.get("/api/attendance/summary", headers=hr).json()
    assert (body["attended_days"], body["absent_days"]) == (1, 1)
    assert body["attendance_percentage"] == 50.0


def test_audit_failure_rolls_back_correction(client, headers_for, record, monkeypatch, session_factory) -> None:
    from app.api import attendance

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(attendance, "record_audit", failing_audit)
    raw_client = TestClient(app, raise_server_exceptions=False)
    response = raw_client.patch(
        f"/api/attendance/{record['id']}/correction",
        json={"check_out": local(2026, 3, 10, 22, 0).isoformat(), "reason": "Should not persist"},
        headers=headers_for(HR),
    )
    assert response.status_code == 500

    with session_factory() as fresh:
        stored = fresh.get(Attendance, record["id"])
        assert stored.work_minutes == 480
        assert stored.check_out == local(2026, 3, 10, 18, 0)
        assert fresh.scalar(select(AuditLog).where(AuditLog.action == "attendance.corrected")) is None


# --- Database constraints --------------------------------------------------------------------------


def test_unique_employee_work_date_constraint(db, make_department, make_employee) -> None:
    employee = make_employee(make_department())
    common = {"employee_id": employee.id, "work_date": date(2026, 3, 10), "status": AttendanceStatus.PRESENT}
    db.add(Attendance(check_in=local(2026, 3, 10, 9), **common))
    db.commit()
    db.add(Attendance(check_in=local(2026, 3, 10, 10), **common))
    with constraint_violation("unique", "attendance_records.employee_id, attendance_records.work_date",
                              "uq_attendance_records_employee_id_work_date"):
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "fields",
    [
        {"check_in": local(2026, 3, 10, 9), "check_out": local(2026, 3, 10, 8)},
        {"check_in": None, "check_out": local(2026, 3, 10, 8)},
        {"check_in": local(2026, 3, 10, 9), "break_minutes": -1},
        {"check_in": local(2026, 3, 10, 9), "work_minutes": -5},
    ],
    ids=["checkout-before-checkin", "checkout-without-checkin", "negative-break", "negative-work"],
)
def test_check_constraints(db, make_department, make_employee, fields) -> None:
    employee = make_employee(make_department())
    db.add(Attendance(employee_id=employee.id, work_date=date(2026, 3, 10), status=AttendanceStatus.PRESENT, **fields))
    with constraint_violation("check"):
        db.commit()
    db.rollback()


def test_requires_authentication(client) -> None:
    for method, path in [("post", "/api/attendance/check-in"), ("post", "/api/attendance/check-out"),
                         ("get", "/api/attendance/me"), ("get", "/api/attendance"),
                         ("get", "/api/attendance/1"), ("patch", "/api/attendance/1/correction")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_no_delete_endpoint(client, headers_for, record) -> None:
    assert client.delete(f"/api/attendance/{record['id']}", headers=headers_for(ADMIN)).status_code == 405
