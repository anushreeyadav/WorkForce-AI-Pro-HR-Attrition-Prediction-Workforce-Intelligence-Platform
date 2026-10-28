"""Leave requests, balances, review workflow, cancellation, conflicts and notifications.

Server "today" is Monday 2026-03-02 (Asia/Kolkata) unless a test moves `leave_clock`.
2026-03-09 is a Monday; 2026-03-14/15 are a weekend.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.models import (
    Attendance,
    AttendanceStatus,
    AuditLog,
    Department,
    EmploymentStatus,
    LeaveBalance,
    LeaveRequest,
    LeaveRequestStatus,
    Notification,
    UserRole,
)
from app.services import leave_balance
from app.services.leave_attendance import approved_leave_dates
from tests.conftest import local

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE


@pytest.fixture
def annual(make_leave_type, make_policy):
    """ANNUAL leave, organization-wide policy: 10 days/year, carry forward up to 5, max 5 consecutive."""
    leave_type = make_leave_type("ANNUAL", "Annual Leave")
    policy = make_policy(leave_type, annual_allowance_days=10, carry_forward_days=5, max_consecutive_days=5)
    return leave_type, policy


def submit(client, headers, leave_type_id, start, end, reason="Family commitments"):
    return client.post("/api/leave/requests", headers=headers, json={
        "leave_type_id": leave_type_id, "start_date": start, "end_date": end, "reason": reason,
    })


def balance(db, employee, leave_type, year=2026) -> LeaveBalance:
    db.expire_all()
    return db.scalar(select(LeaveBalance).where(
        LeaveBalance.employee_id == employee.id, LeaveBalance.leave_type_id == leave_type.id, LeaveBalance.year == year))


def act(client, headers, request_id, action, **body):
    return client.post(f"/api/leave/requests/{request_id}/{action}", json=body, headers=headers)


# --- Submission ---------------------------------------------------------------------------------


def test_submit_counts_working_days_and_reserves_balance(client, db, staff, annual, leave_clock) -> None:
    leave_type, policy = annual
    employee, headers = staff()
    response = submit(client, headers, leave_type.id, "2026-03-13", "2026-03-16")  # Fri - Mon

    assert response.status_code == 201
    body = response.json()
    assert body["working_days"] == 2
    assert body["status"] == "PENDING"
    assert body["employee"]["id"] == employee.id
    assert body["leave_type"]["code"] == "ANNUAL" and body["policy_id"] == policy.id

    stored = balance(db, employee, leave_type)
    assert (stored.allocated_days, stored.pending_days, stored.used_days) == (10, 2, 0)

    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave.requested"))
    assert log.details == {"employee_id": employee.id, "leave_type": "ANNUAL", "start_date": "2026-03-13",
                           "end_date": "2026-03-16", "working_days": 2}
    assert "Family" not in str(log.details)  # the reason stays out of the audit log


def test_weekend_only_request_rejected(client, staff, annual, leave_clock) -> None:
    _, headers = staff()
    response = submit(client, headers, annual[0].id, "2026-03-14", "2026-03-15")
    assert response.status_code == 422
    assert response.json() == {"detail": "The selected dates contain no working days"}


def test_policy_counting_weekends(client, staff, make_leave_type, make_policy, leave_clock) -> None:
    maternity = make_leave_type("MATERNITY", "Maternity Leave")
    make_policy(maternity, annual_allowance_days=180, count_weekends=True)
    _, headers = staff()
    assert submit(client, headers, maternity.id, "2026-03-09", "2026-03-15").json()["working_days"] == 7


def test_department_policy_beats_org_policy(client, db, staff, annual, make_policy, leave_clock) -> None:
    leave_type, _ = annual
    employee, headers = staff(department="Engineering")
    make_policy(leave_type, department=db.get(Department, employee.department_id), annual_allowance_days=2)
    response = submit(client, headers, leave_type.id, "2026-03-09", "2026-03-11")  # 3 days > 2
    assert response.status_code == 422
    assert response.json() == {"detail": "Insufficient leave balance: 2 day(s) available"}


@pytest.mark.parametrize(
    "start, end, detail",
    [
        ("2026-03-10", "2026-03-09", None),
        ("2026-12-31", "2027-01-01", None),
        ("2026-01-15", "2026-01-16", "Leave cannot start more than 30 days in the past"),
        ("2028-01-03", "2028-01-04", "Leave can be requested for this year or next year only"),
        ("2026-03-09", "2026-03-16", "This leave type allows at most 5 consecutive day(s)"),  # 6 working days
        ("2025-12-29", "2025-12-31", None),
    ],
    ids=["reversed", "crosses-year", "too-far-back", "too-far-ahead", "max-consecutive", "previous-year"],
)
def test_invalid_dates(client, staff, annual, leave_clock, start, end, detail) -> None:
    _, headers = staff()
    response = submit(client, headers, annual[0].id, start, end)
    assert response.status_code == 422
    if detail:
        assert response.json() == {"detail": detail}


def test_backdating_within_limit_allowed(client, staff, annual, leave_clock) -> None:
    _, headers = staff()
    assert submit(client, headers, annual[0].id, "2026-02-27", "2026-02-27").status_code == 201


def test_no_applicable_policy(client, staff, make_leave_type, make_policy, leave_clock) -> None:
    unpaid = make_leave_type("UNPAID", is_paid=False)
    make_policy(unpaid, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 10))
    no_policy = make_leave_type("STUDY")
    _, headers = staff()
    assert submit(client, headers, no_policy.id, "2026-03-09", "2026-03-09").json() == {
        "detail": "No active leave policy covers these dates"}
    # Policy ends on the 10th, so a request running to the 11th is not covered.
    assert submit(client, headers, unpaid.id, "2026-03-09", "2026-03-11").status_code == 422
    assert submit(client, headers, unpaid.id, "2026-03-09", "2026-03-10").status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {"employee_id": 99},
        {"status": "APPROVED"},
        {"working_days": 1},
        {"reason": "no"},
        {"reason": None},
    ],
    ids=["employee-id", "status", "working-days", "short-reason", "null-reason"],
)
def test_client_cannot_choose_identity_status_or_days(client, staff, annual, leave_clock, body) -> None:
    _, headers = staff()
    payload = {"leave_type_id": annual[0].id, "start_date": "2026-03-09", "end_date": "2026-03-09",
               "reason": "Family commitments", **body}
    assert client.post("/api/leave/requests", json=payload, headers=headers).status_code == 422


def test_inactive_leave_type_and_inactive_employee(client, staff, make_leave_type, make_policy, annual, leave_clock) -> None:
    old = make_leave_type("OLD", is_active=False)
    make_policy(old)
    _, headers = staff()
    assert submit(client, headers, old.id, "2026-03-09", "2026-03-09").status_code == 422
    _, inactive = staff(status=EmploymentStatus.INACTIVE)
    assert submit(client, inactive, annual[0].id, "2026-03-09", "2026-03-09").status_code == 409


@pytest.mark.parametrize(
    "method, path",
    [("post", "/api/leave/requests"), ("get", "/api/leave/requests/me"), ("get", "/api/leave/balances/me")],
)
def test_no_linked_employee_404(client, headers_for, annual, leave_clock, method, path) -> None:
    kwargs = {"json": {"leave_type_id": annual[0].id, "start_date": "2026-03-09", "end_date": "2026-03-09",
                       "reason": "Family commitments"}} if method == "post" else {}
    response = getattr(client, method)(path, headers=headers_for(HR), **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "No employee profile is linked to your account"}


def test_overlapping_requests(client, headers_for, staff, annual, leave_clock) -> None:
    leave_type = annual[0]
    _, headers = staff()
    first = submit(client, headers, leave_type.id, "2026-03-09", "2026-03-11").json()

    overlap = submit(client, headers, leave_type.id, "2026-03-11", "2026-03-12")
    assert overlap.status_code == 409
    assert submit(client, headers, leave_type.id, "2026-03-12", "2026-03-13").status_code == 201  # adjacent is fine

    # Rejected or cancelled requests no longer block the dates.
    act(client, headers_for(HR), first["id"], "reject", comment="Team offsite that week")
    assert submit(client, headers, leave_type.id, "2026-03-10", "2026-03-10").status_code == 201


def test_insufficient_balance(client, db, staff, annual, leave_clock) -> None:
    leave_type = annual[0]
    employee, headers = staff()
    assert submit(client, headers, leave_type.id, "2026-03-09", "2026-03-13").status_code == 201  # 5
    assert submit(client, headers, leave_type.id, "2026-03-16", "2026-03-20").status_code == 201  # 5
    response = submit(client, headers, leave_type.id, "2026-03-23", "2026-03-23")
    assert response.status_code == 422
    assert response.json() == {"detail": "Insufficient leave balance: 0 day(s) available"}
    stored = balance(db, employee, leave_type)
    assert (stored.pending_days, stored.used_days) == (10, 0)
    assert db.scalar(select(LeaveRequest).where(LeaveRequest.start_date == date(2026, 3, 23))) is None


# --- Balances -----------------------------------------------------------------------------------------


def test_balances_me_is_read_only_and_reflects_requests(client, db, staff, annual, leave_clock) -> None:
    leave_type = annual[0]
    employee, headers = staff()
    before = client.get("/api/leave/balances/me", headers=headers).json()
    assert before == [{
        "leave_type": {"id": leave_type.id, "code": "ANNUAL", "name": "Annual Leave"}, "year": 2026,
        "allocated_days": 10, "carried_forward_days": 0, "used_days": 0, "pending_days": 0,
        "available_days": 10, "policy_id": annual[1].id,
    }]
    assert balance(db, employee, leave_type) is None  # viewing never creates rows

    submit(client, headers, leave_type.id, "2026-03-09", "2026-03-10")
    after = client.get("/api/leave/balances/me", headers=headers).json()[0]
    assert (after["pending_days"], after["available_days"]) == (2, 8)


def test_carry_forward_is_capped(client, db, staff, annual, leave_clock) -> None:
    leave_type = annual[0]
    employee, headers = staff()
    db.add(LeaveBalance(employee_id=employee.id, leave_type_id=leave_type.id, year=2025,
                        allocated_days=10, carried_forward_days=0, used_days=2, pending_days=0))
    db.commit()
    view = client.get("/api/leave/balances/me", headers=headers).json()[0]
    assert (view["carried_forward_days"], view["available_days"]) == (5, 15)  # 8 unused, capped at 5

    submit(client, headers, leave_type.id, "2026-03-09", "2026-03-09")
    assert balance(db, employee, leave_type).carried_forward_days == 5


def test_balance_year_param(client, staff, annual, leave_clock) -> None:
    _, headers = staff()
    assert client.get("/api/leave/balances/me", params={"year": 1999}, headers=headers).status_code == 422
    assert client.get("/api/leave/balances/me", params={"year": 2025}, headers=headers).json() == []  # no policy in 2025


# --- Review workflow --------------------------------------------------------------------------------


@pytest.fixture
def pending(client, staff, annual, leave_clock):
    """An Engineering employee with a pending 3-day request (Mon 9 - Wed 11 March)."""
    employee, headers = staff(department="Engineering")
    body = submit(client, headers, annual[0].id, "2026-03-09", "2026-03-11").json()
    return {"employee": employee, "headers": headers, "id": body["id"], "leave_type": annual[0]}


def test_hr_approves(client, db, headers_for, pending) -> None:
    response = act(client, headers_for(HR), pending["id"], "approve", comment="Enjoy")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPROVED" and body["review_comment"] == "Enjoy"
    assert body["reviewed_by_user_id"] is not None and body["reviewed_at"] is not None

    stored = balance(db, pending["employee"], pending["leave_type"])
    assert (stored.pending_days, stored.used_days) == (0, 3)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave.approved"))
    assert log.details == {"employee_id": pending["employee"].id, "working_days": 3, "attendance_conflict_dates": []}
    note = db.scalar(select(Notification).where(Notification.user_id == pending["employee"].user_id))
    assert note.title == "Leave approved"
    assert note.message == "Your Annual Leave request for 2026-03-09 to 2026-03-11 was approved."


def test_reject_requires_comment_and_releases_balance(client, db, headers_for, pending) -> None:
    hr = headers_for(HR)
    assert act(client, hr, pending["id"], "reject").status_code == 422
    assert act(client, hr, pending["id"], "reject", comment="no").status_code == 422
    response = act(client, hr, pending["id"], "reject", comment="Release deadline that week")
    assert response.status_code == 200 and response.json()["status"] == "REJECTED"
    stored = balance(db, pending["employee"], pending["leave_type"])
    assert (stored.pending_days, stored.used_days) == (0, 0)
    assert db.scalar(select(AuditLog).where(AuditLog.action == "leave.rejected")) is not None


@pytest.mark.parametrize("role, expected", [(ADMIN, 200), (HR, 200), (MANAGER, 404), (EMPLOYEE, 403)])
def test_review_permissions_for_all_roles(client, headers_for, pending, role, expected) -> None:
    # A MANAGER with no employee record in Engineering is out of scope (404); EMPLOYEE cannot review at all.
    assert act(client, headers_for(role), pending["id"], "approve").status_code == expected


def test_manager_department_scope(client, staff, annual, leave_clock, pending) -> None:
    _, own_manager = staff(MANAGER, department="Engineering")
    _, other_manager = staff(MANAGER, department="Sales")

    assert act(client, other_manager, pending["id"], "approve").status_code == 404
    queue = client.get("/api/leave/requests", headers=other_manager).json()
    assert queue["total"] == 0
    assert client.get(f"/api/leave/requests/{pending['id']}", headers=other_manager).status_code == 404

    assert client.get("/api/leave/requests", headers=own_manager).json()["total"] == 1
    assert act(client, own_manager, pending["id"], "approve").status_code == 200


def test_manager_notified_on_submission(client, db, staff, annual, leave_clock) -> None:
    manager, _ = staff(MANAGER, department="Engineering")
    staff(MANAGER, department="Sales")
    employee, headers = staff(department="Engineering")
    request_id = submit(client, headers, annual[0].id, "2026-03-09", "2026-03-09").json()["id"]
    notes = db.scalars(select(Notification)).all()
    assert [(n.user_id, n.title, n.entity_id) for n in notes] == [
        (manager.user_id, "Leave request awaiting review", str(request_id))]
    assert "Family" not in notes[0].message


@pytest.mark.parametrize("role", [HR, MANAGER, ADMIN])
def test_no_self_approval(client, staff, annual, leave_clock, role) -> None:
    _, headers = staff(role, department="Engineering")
    request_id = submit(client, headers, annual[0].id, "2026-03-09", "2026-03-09").json()["id"]
    for action, body in (("approve", {}), ("reject", {"comment": "Rejecting myself"})):
        response = act(client, headers, request_id, action, **body)
        assert response.status_code == 403
        assert response.json() == {"detail": "You cannot review your own leave request"}


def test_already_finalized(client, headers_for, pending) -> None:
    hr = headers_for(HR)
    assert act(client, hr, pending["id"], "approve").status_code == 200
    again = act(client, hr, pending["id"], "approve")
    assert again.status_code == 409
    assert again.json() == {"detail": "Leave request is already approved"}
    assert act(client, hr, pending["id"], "reject", comment="Changed my mind").status_code == 409


def test_concurrent_approvals_cannot_double_spend(client, db, headers_for, pending, monkeypatch) -> None:
    """Simulate two reviewers who both passed the PENDING check before either committed.

    The status UPDATE is conditional (WHERE status = 'PENDING'), so only one can succeed, and the
    balance moves exactly once.
    """
    from app.api import leave

    monkeypatch.setattr(leave, "_check_review_access", lambda db, reviewer, request: None)
    hr = headers_for(HR)
    assert act(client, hr, pending["id"], "approve").status_code == 200
    second = act(client, hr, pending["id"], "approve")
    assert second.status_code == 409
    assert second.json() == {"detail": "This leave request was already processed"}
    stored = balance(db, pending["employee"], pending["leave_type"])
    assert (stored.pending_days, stored.used_days) == (0, 3)
    assert len(db.scalars(select(AuditLog).where(AuditLog.action == "leave.approved")).all()) == 1


def test_stale_balance_cannot_overspend(db, make_department, make_employee, make_leave_type, make_policy) -> None:
    """Two transactions both read 'available = 5' and each try to reserve 3. The conditional UPDATE
    re-checks availability in the database, so the second fails instead of overspending."""
    employee = make_employee(make_department())
    leave_type = make_leave_type()
    policy = make_policy(leave_type, annual_allowance_days=5)
    row = leave_balance.ensure_balance(db, employee.id, policy, 2026)
    db.commit()
    stale_copy_a = row
    stale_copy_b = db.get(LeaveBalance, row.id)

    leave_balance.reserve(db, stale_copy_a, 3)
    with pytest.raises(leave_balance.InsufficientBalanceError) as exc_info:
        leave_balance.reserve(db, stale_copy_b, 3)
    assert exc_info.value.available == 2
    db.commit()
    assert balance(db, employee, leave_type).pending_days == 3


def test_overspend_check_constraint(db, make_department, make_employee, make_leave_type) -> None:
    employee = make_employee(make_department())
    leave_type = make_leave_type()
    db.add(LeaveBalance(employee_id=employee.id, leave_type_id=leave_type.id, year=2026,
                        allocated_days=5, carried_forward_days=0, used_days=4, pending_days=2))
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        db.commit()
    db.rollback()


def test_unique_balance_per_employee_type_year(db, make_department, make_employee, make_leave_type) -> None:
    employee = make_employee(make_department())
    leave_type = make_leave_type()
    for _ in range(2):
        db.add(LeaveBalance(employee_id=employee.id, leave_type_id=leave_type.id, year=2026, allocated_days=5))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        db.commit()
    db.rollback()


def test_leave_request_check_constraints(db, make_department, make_employee, make_leave_type, make_policy) -> None:
    employee = make_employee(make_department())
    leave_type = make_leave_type()
    policy = make_policy(leave_type)
    for start, end, days in ((date(2026, 3, 10), date(2026, 3, 9), 1), (date(2026, 3, 9), date(2026, 3, 9), 0)):
        db.add(LeaveRequest(employee_id=employee.id, leave_type_id=leave_type.id, policy_id=policy.id,
                            start_date=start, end_date=end, working_days=days, reason="Check"))
        with pytest.raises(IntegrityError, match="CHECK constraint failed"):
            db.commit()
        db.rollback()


def test_audit_failure_rolls_back_approval(client, headers_for, pending, monkeypatch, session_factory) -> None:
    from app.api import leave

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(leave, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/leave/requests/{pending['id']}/approve", json={}, headers=headers_for(HR))
    assert response.status_code == 500

    with session_factory() as fresh:
        assert fresh.get(LeaveRequest, pending["id"]).status == LeaveRequestStatus.PENDING
        stored = fresh.scalar(select(LeaveBalance).where(LeaveBalance.employee_id == pending["employee"].id))
        assert (stored.pending_days, stored.used_days) == (3, 0)
        assert fresh.scalar(select(Notification).where(Notification.title == "Leave approved")) is None


@pytest.fixture
def strict_db():
    """A separate in-memory SQLite database with real transaction semantics.

    pysqlite does not BEGIN before a SAVEPOINT, so on the shared test database a released savepoint
    (used when a balance row is first created) commits immediately. Here SQLAlchemy's documented
    recipe takes over BEGIN so savepoints nest inside the transaction, as they do on MySQL/InnoDB.
    Setup uses short-lived sessions so no transaction is open on the shared connection during requests.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.database import get_db
    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _no_driver_transactions(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(connection):
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    yield factory
    engine.dispose()


def _strict_setup(factory) -> tuple[dict[str, str], int]:
    from app.core.security import create_access_token, hash_password
    from app.models import Employee, LeavePolicy, LeaveType, User
    from tests.conftest import auth_header

    with factory() as s:
        user = User(full_name="Strict", email="strict@example.com", password_hash=hash_password("Correct-Horse-Battery-9"))
        department = Department(name="Engineering")
        s.add_all([user, department])
        s.flush()
        s.add(Employee(employee_code="STR-1", full_name="Strict", work_email="strict@corp.example.com",
                       department_id=department.id, designation="Engineer", date_of_joining=date(2024, 1, 1),
                       user_id=user.id))
        leave_type = LeaveType(code="ANNUAL", name="Annual Leave")
        s.add(leave_type)
        s.flush()
        s.add(LeavePolicy(leave_type_id=leave_type.id, annual_allowance_days=10, effective_from=date(2026, 1, 1)))
        s.commit()
        return auth_header(create_access_token(user.id).token), leave_type.id


def test_strict_db_submission_commits_balance(strict_db, leave_clock) -> None:
    """Control: on the strict database the normal path (including the savepoint) commits correctly."""
    headers, leave_type_id = _strict_setup(strict_db)
    response = TestClient(app).post("/api/leave/requests", headers=headers, json={
        "leave_type_id": leave_type_id, "start_date": "2026-03-09", "end_date": "2026-03-10", "reason": "Family commitments"})
    assert response.status_code == 201
    with strict_db() as fresh:
        assert fresh.scalar(select(LeaveBalance)).pending_days == 2


def test_audit_failure_rolls_back_submission(strict_db, leave_clock, monkeypatch) -> None:
    from app.api import leave

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(leave, "record_audit", failing_audit)
    headers, leave_type_id = _strict_setup(strict_db)
    response = TestClient(app, raise_server_exceptions=False).post("/api/leave/requests", headers=headers, json={
        "leave_type_id": leave_type_id, "start_date": "2026-03-09", "end_date": "2026-03-10", "reason": "Family commitments"})
    assert response.status_code == 500
    with strict_db() as fresh:
        assert fresh.scalar(select(LeaveRequest)) is None
        # The balance row created on first use (inside a savepoint) is rolled back with everything else.
        assert fresh.scalar(select(LeaveBalance)) is None


# --- Viewing -----------------------------------------------------------------------------------------


def test_owner_and_others_viewing(client, staff, headers_for, pending) -> None:
    _, colleague = staff(department="Engineering")
    assert client.get(f"/api/leave/requests/{pending['id']}", headers=pending["headers"]).status_code == 200
    assert client.get(f"/api/leave/requests/{pending['id']}", headers=colleague).status_code == 404
    assert client.get(f"/api/leave/requests/{pending['id']}", headers=headers_for(HR)).status_code == 200
    assert client.get("/api/leave/requests", headers=colleague).status_code == 403

    mine = client.get("/api/leave/requests/me", headers=pending["headers"]).json()
    assert [r["id"] for r in mine["items"]] == [pending["id"]]
    assert client.get("/api/leave/requests/me", headers=colleague).json()["total"] == 0


def test_review_queue_filters(client, headers_for, staff, annual, pending) -> None:
    _, other = staff(department="Sales")
    submit(client, other, annual[0].id, "2026-04-06", "2026-04-07")
    hr = headers_for(HR)

    def total(**params):
        return client.get("/api/leave/requests", params=params, headers=hr).json()["total"]

    assert total() == 2
    assert total(employee_id=pending["employee"].id) == 1
    assert total(department_id=pending["employee"].department_id) == 1
    assert total(date_from="2026-04-01") == 1
    assert total(date_to="2026-03-09") == 1
    act(client, hr, pending["id"], "approve")
    assert total(status="PENDING") == 1 and total(status="APPROVED") == 1
    assert client.get("/api/leave/requests", params={"date_from": "2026-05-01", "date_to": "2026-04-01"},
                      headers=hr).status_code == 422


# --- Cancellation -------------------------------------------------------------------------------------


def test_owner_cancels_pending(client, db, pending) -> None:
    response = act(client, pending["headers"], pending["id"], "cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED" and body["cancelled_at"] is not None
    assert balance(db, pending["employee"], pending["leave_type"]).pending_days == 0
    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave.cancelled"))
    assert log.details == {"employee_id": pending["employee"].id, "previous_status": "PENDING",
                           "working_days": 3, "cancelled_by_owner": True}


def test_owner_cancels_approved_before_start(client, db, headers_for, pending) -> None:
    act(client, headers_for(HR), pending["id"], "approve")
    response = act(client, pending["headers"], pending["id"], "cancel")
    assert response.status_code == 200
    stored = balance(db, pending["employee"], pending["leave_type"])
    assert (stored.used_days, stored.pending_days) == (0, 0)


def test_owner_cannot_cancel_started_approved_leave(client, headers_for, pending, leave_clock) -> None:
    act(client, headers_for(HR), pending["id"], "approve")
    leave_clock.set(local(2026, 3, 9, 8, 0))  # start date, org time
    response = act(client, pending["headers"], pending["id"], "cancel")
    assert response.status_code == 409
    assert response.json() == {"detail": "Approved leave that has already started can only be cancelled by HR"}


def test_hr_cancels_started_leave_with_comment(client, db, headers_for, pending, leave_clock) -> None:
    hr = headers_for(HR)
    act(client, hr, pending["id"], "approve")
    leave_clock.set(local(2026, 3, 10, 12, 0))
    assert act(client, hr, pending["id"], "cancel").status_code == 422  # comment required
    response = act(client, hr, pending["id"], "cancel", comment="Recalled for an emergency release")
    assert response.status_code == 200
    assert response.json()["cancellation_comment"] == "Recalled for an emergency release"
    assert balance(db, pending["employee"], pending["leave_type"]).used_days == 0
    note = db.scalar(select(Notification).where(Notification.title == "Leave cancelled"))
    assert note.user_id == pending["employee"].user_id


def test_hr_cannot_use_privileged_cancel_on_own_leave(client, headers_for, staff, annual, leave_clock) -> None:
    _, hr_self = staff(HR)
    request_id = submit(client, hr_self, annual[0].id, "2026-03-09", "2026-03-09").json()["id"]
    act(client, headers_for(ADMIN), request_id, "approve")
    leave_clock.set(local(2026, 3, 9, 12, 0))
    assert act(client, hr_self, request_id, "cancel", comment="Cancelling my own").status_code == 409


def test_others_cannot_cancel(client, staff, pending) -> None:
    _, colleague = staff(department="Engineering")
    _, manager = staff(MANAGER, department="Engineering")
    assert act(client, colleague, pending["id"], "cancel").status_code == 404
    forbidden = act(client, manager, pending["id"], "cancel", comment="Manager tries to cancel")
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Only the employee or HR can cancel this request"}


@pytest.mark.parametrize("first", ["reject", "cancel"])
def test_cannot_cancel_finalized(client, headers_for, pending, first) -> None:
    if first == "reject":
        act(client, headers_for(HR), pending["id"], "reject", comment="Not this week")
    else:
        act(client, pending["headers"], pending["id"], "cancel")
    assert act(client, pending["headers"], pending["id"], "cancel").status_code == 409


# --- Attendance integration -----------------------------------------------------------------------


def test_attendance_conflicts_flagged_not_changed(client, db, headers_for, staff, pending) -> None:
    employee = pending["employee"]
    attendance = Attendance(employee_id=employee.id, work_date=date(2026, 3, 10), status=AttendanceStatus.PRESENT,
                            check_in=local(2026, 3, 10, 9, 0), check_out=local(2026, 3, 10, 18, 0),
                            break_minutes=60, work_minutes=480, overtime_minutes=0)
    db.add(attendance)
    db.commit()
    hr = headers_for(HR)

    act(client, hr, pending["id"], "approve")
    log = db.scalar(select(AuditLog).where(AuditLog.action == "leave.approved"))
    assert log.details["attendance_conflict_dates"] == ["2026-03-10"]

    conflicts = client.get("/api/leave/conflicts", headers=hr).json()
    assert conflicts["total"] == 1
    item = conflicts["items"][0]
    assert (item["attendance_id"], item["leave_request_id"], item["work_date"]) == (attendance.id, pending["id"], "2026-03-10")
    assert item["leave_type"]["code"] == "ANNUAL" and item["attendance_status"] == "PRESENT"

    # The attendance record is untouched and nothing was fabricated for the other leave days.
    db.expire_all()
    assert db.get(Attendance, attendance.id).work_minutes == 480
    assert len(db.scalars(select(Attendance)).all()) == 1

    _, sales_manager = staff(MANAGER, department="Sales")
    assert client.get("/api/leave/conflicts", headers=sales_manager).json()["total"] == 0
    assert client.get("/api/leave/conflicts", headers=pending["headers"]).status_code == 403


def test_approved_leave_dates_helper(client, db, headers_for, pending, staff, annual) -> None:
    covered_before = approved_leave_dates(db, pending["employee"].id, date(2026, 3, 1), date(2026, 3, 31))
    assert covered_before == {}  # pending leave is not "on leave"
    act(client, headers_for(HR), pending["id"], "approve")
    covered = approved_leave_dates(db, pending["employee"].id, date(2026, 3, 10), date(2026, 3, 31))
    assert covered == {date(2026, 3, 10): pending["id"], date(2026, 3, 11): pending["id"]}


# --- Notifications ------------------------------------------------------------------------------------


def test_notifications_read_unread_and_isolation(client, staff, headers_for, pending) -> None:
    act(client, headers_for(HR), pending["id"], "approve")
    headers = pending["headers"]
    listing = client.get("/api/notifications/me", headers=headers).json()
    assert listing["total"] == 1
    note = listing["items"][0]
    assert (note["is_read"], note["entity_type"], note["entity_id"]) == (False, "leave_request", str(pending["id"]))

    _, someone_else = staff()
    assert client.post(f"/api/notifications/{note['id']}/read", headers=someone_else).status_code == 404
    assert client.get("/api/notifications/me", headers=someone_else).json()["total"] == 0

    read = client.post(f"/api/notifications/{note['id']}/read", headers=headers).json()
    assert read["is_read"] is True and read["read_at"] is not None
    assert client.get("/api/notifications/me", params={"unread_only": "true"}, headers=headers).json()["total"] == 0
    assert client.post("/api/notifications/999/read", headers=headers).status_code == 404


def test_leave_endpoints_require_authentication(client) -> None:
    for method, path in [("post", "/api/leave/requests"), ("get", "/api/leave/requests/me"),
                         ("get", "/api/leave/requests"), ("post", "/api/leave/requests/1/approve"),
                         ("post", "/api/leave/requests/1/cancel"), ("get", "/api/leave/balances/me"),
                         ("get", "/api/leave/conflicts"), ("get", "/api/notifications/me")]:
        assert getattr(client, method)(path).status_code == 401, path
