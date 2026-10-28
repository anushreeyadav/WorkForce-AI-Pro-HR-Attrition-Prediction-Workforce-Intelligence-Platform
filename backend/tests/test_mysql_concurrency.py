"""Opt-in MySQL integration tests (skipped unless tests/mysql_guard.py positively identifies a disposable database).

Two kinds:
- read-only checks inside the usual rolled-back transaction (schema drift, DATETIME(6), utf8mb4);
- `mysql_commits` tests: real concurrent requests on separate pooled connections, released together by a barrier,
  with committed data. Each deletes exactly the rows it created (id high-water marks taken before the test).
All data is synthetic. Nothing here creates, migrates or drops schema objects.
"""

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, select, text, update
from sqlalchemy.orm import sessionmaker

from app.core import security
from app.core.database import get_db
from app.main import app
from app.models import (
    AuditLog,
    Base,
    Department,
    Employee,
    LeavePolicy,
    LeaveRequest,
    LeaveType,
    RevokedToken,
    Task,
    TaskEvent,
    TaskEventType,
    User,
    UserRole,
)
from tests.conftest import TEST_PASSWORD, auth_header
from tests.mysql_guard import MARKER_TABLE

pytestmark = pytest.mark.mysql_only
ROUNDS = 8


# --- Read-only checks (inside the rolled-back transaction) -------------------------------------------------------


def test_live_schema_matches_the_models(mysql_engine) -> None:
    with mysql_engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={
            "compare_type": True,
            "include_name": lambda name, type_, _: not (type_ == "table" and name == MARKER_TABLE)})
        assert compare_metadata(context, Base.metadata) == []
        assert conn.execute(text("SELECT @@version")).scalar_one().startswith("8.")


def test_readiness_on_the_migrated_database(client) -> None:
    """/api/health/ready on a database migrated with `alembic upgrade head` (read-only)."""
    assert client.get("/api/health/ready").json() == {"status": "ready", "database": "reachable", "schema": "current"}


def test_timestamps_keep_microseconds(mysql_engine, db) -> None:
    columns = inspect(db.connection()).get_columns("audit_logs")
    assert next(c for c in columns if c["name"] == "created_at")["type"].fsp == 6  # DATETIME(6)
    moment = datetime(2026, 3, 10, 9, 30, 15, 987654, tzinfo=UTC)
    db.add(AuditLog(action="auth.logout", entity_type="user", entity_id="1", created_at=moment))
    db.commit()
    db.expire_all()
    assert db.scalar(select(AuditLog.created_at).where(AuditLog.created_at == moment)) == moment


def test_utf8mb4_text_round_trips(db, make_department) -> None:
    name = "Ops 🚀 संचालन Δ"
    department = make_department(name)
    db.expire_all()
    assert db.get(Department, department.id).name == name


# --- Concurrent requests on separate connections -----------------------------------------------------------------

ID_TABLES = [t for t in reversed(Base.metadata.sorted_tables) if "id" in t.c]


@pytest.fixture
def committed(mysql_engine):
    """A session factory on real pooled connections, an API client using it, and exact cleanup afterwards."""
    factory = sessionmaker(bind=mysql_engine, autoflush=False, expire_on_commit=False)
    with mysql_engine.connect() as conn:
        marks = {t.name: conn.execute(select(func.coalesce(func.max(t.c.id), 0))).scalar_one() for t in ID_TABLES}

    def _get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    yield factory, TestClient(app, raise_server_exceptions=False)  # a server error is an outcome to assert on
    app.dependency_overrides.clear()
    with mysql_engine.begin() as conn:
        new_users = select(User.id).where(User.id > marks["users"]).scalar_subquery()
        conn.execute(delete(RevokedToken).where(RevokedToken.user_id.in_(new_users)))
        conn.execute(update(Employee).where(Employee.id > marks["employees"]).values(user_id=None))
        for table in ID_TABLES:
            conn.execute(delete(table).where(table.c.id > marks[table.name]))
        assert conn.execute(select(func.count()).select_from(User)).scalar_one() == 0  # back to the verified state


def _user(factory, email, role, active=True) -> User:
    with factory() as s:
        user = User(full_name=email.split("@")[0], email=email, role=role, is_active=active,
                    password_hash=security.hash_password(TEST_PASSWORD))
        s.add(user)
        s.commit()
        return user


def _token(user: User, factory=None) -> dict[str, str]:
    """A valid token for the account's CURRENT session version (deactivation bumps it, ending older tokens)."""
    version = 0
    if factory is not None:
        with factory() as s:
            version = s.scalar(select(User.token_version).where(User.id == user.id))
    return auth_header(security.create_access_token(user.id, token_version=version).token)


def _together(n, call):
    """Run call(client, i) for i in range(n) in threads released at the same instant, each thread with its OWN test
    client (a shared TestClient partly serializes requests); return the results."""
    barrier = threading.Barrier(n)
    clients = [TestClient(app, raise_server_exceptions=False) for _ in range(n)]

    def run(i):
        barrier.wait()
        return call(clients[i], i)

    with ThreadPoolExecutor(n) as pool:
        return list(pool.map(run, range(n)))


@pytest.mark.mysql_commits
def test_two_admins_cannot_remove_each_other_concurrently(committed) -> None:
    """The last-admin guard under real row locks: never both succeed, never zero active administrators, and a
    conflict is a 409 (deadlocks are mapped by app.main), never a 5xx."""
    factory, client = committed
    a, b = _user(factory, "admin.a@example.com", UserRole.ADMIN), _user(factory, "admin.b@example.com", UserRole.ADMIN)
    outcomes = Counter()
    for _ in range(ROUNDS):
        heads = [_token(a, factory), _token(b, factory)]
        targets = [b.id, a.id]
        responses = _together(2, lambda c, i: c.patch(f"/api/users/{targets[i]}/status", headers=heads[i],
                                                         json={"is_active": False}))
        codes = [r.status_code for r in responses]
        outcomes[tuple(sorted(f"{r.status_code} {r.json().get('detail', '') if r.status_code != 200 else ''}".strip()
                              for r in responses))] += 1
        assert codes.count(200) <= 1 and all(c < 500 for c in codes), codes
        with factory() as s:
            assert s.scalar(select(func.count()).select_from(User).where(User.role == UserRole.ADMIN,
                                                                          User.is_active.is_(True))) >= 1
            s.execute(update(User).where(User.id.in_([a.id, b.id])).values(is_active=True))
            s.commit()
    print("admin race outcomes:", dict(outcomes))


def _leave_setup(factory, suffix):
    with factory() as s:
        department = Department(name=f"Concurrency Dept {suffix}")
        leave_type = LeaveType(code=f"CONC{suffix}", name=f"Concurrency leave {suffix}", is_paid=True)
        s.add_all([department, leave_type])
        s.flush()
        s.add(LeavePolicy(leave_type_id=leave_type.id, annual_allowance_days=20,
                          effective_from=date(date.today().year, 1, 1)))
        s.commit()
    staff = _user(factory, f"leave.racer{suffix}@example.com", UserRole.EMPLOYEE)
    with factory() as s:
        s.add(Employee(employee_code=f"CONC-{suffix}", full_name="Leave Racer",
                       work_email=f"leave.racer{suffix}@corp.example.com", department_id=department.id,
                       designation="Engineer", date_of_joining=date(2024, 1, 1), user_id=staff.id))
        s.commit()
    return leave_type.id, _token(staff)


def _weekdays(count: int, skip: int = 0) -> list[date]:
    """`count` distinct future weekdays after the first `skip` ones (the leave policy covers the current year)."""
    days, day = [], date.today()
    while len(days) < count + skip:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    assert days[-1].year == date.today().year, "run these tests before late December"
    return days[skip:]


def _pending_days(factory, leave_type_id):
    from app.models import LeaveBalance

    with factory() as s:
        return s.scalar(select(LeaveBalance.pending_days).where(LeaveBalance.leave_type_id == leave_type_id))


@pytest.mark.mysql_commits
def test_overlapping_leave_requests_create_exactly_one(committed) -> None:
    """A double submission (e.g. double-click) of the same dates while the balance row already exists: one request,
    and the days are reserved once."""
    factory, client = committed
    leave_type_id, headers = _leave_setup(factory, "A")
    first, *round_days = _weekdays(1 + ROUNDS // 2, skip=1)
    warm_up = client.post("/api/leave/requests", headers=headers, json={
        "leave_type_id": leave_type_id, "start_date": first.isoformat(), "end_date": first.isoformat(),
        "reason": "Warm-up"})
    assert warm_up.status_code == 201, warm_up.text  # the balance row now exists
    for round_ in range(ROUNDS // 2):
        day = round_days[round_]
        body = {"leave_type_id": leave_type_id, "start_date": day.isoformat(), "end_date": day.isoformat(),
                "reason": "Concurrency test"}
        codes = _together(4, lambda c, i: c.post("/api/leave/requests", headers=headers, json=body).status_code)
        assert sorted(codes) == [201, 409, 409, 409], codes
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(LeaveRequest)
                        .where(LeaveRequest.reason == "Concurrency test")) == ROUNDS // 2
    assert _pending_days(factory, leave_type_id) == 1 + ROUNDS // 2


@pytest.mark.mysql_commits
def test_first_leave_requests_race_creates_one_balance(committed) -> None:
    """The very first requests of a leave type in a year race to create the balance row."""
    factory, client = committed
    leave_type_id, headers = _leave_setup(factory, "B")
    days = _weekdays(4, skip=1)  # different, non-overlapping dates
    responses = _together(4, lambda c, i: c.post("/api/leave/requests", headers=headers, json={
        "leave_type_id": leave_type_id, "start_date": days[i].isoformat(), "end_date": days[i].isoformat(),
        "reason": "First race"}))
    assert [r.status_code for r in responses] == [201] * 4, [(r.status_code, r.text[:200]) for r in responses]
    assert _pending_days(factory, leave_type_id) == 4


@pytest.mark.mysql_commits
def test_duplicate_registration_creates_one_account(committed) -> None:
    _, client = committed
    body = {"full_name": "Twin", "email": "twin@example.com", "password": TEST_PASSWORD}
    codes = _together(4, lambda c, i: c.post("/api/auth/register", json=body).status_code)
    assert sorted(codes) == [201, 409, 409, 409], codes


@pytest.mark.mysql_commits
def test_stale_version_race_has_one_winner(committed) -> None:
    """Optimistic concurrency (expected_version) across real connections: two starts of the same task at once must
    produce exactly one state change and one history event."""
    factory, client = committed
    hr = _user(factory, "race.hr@example.com", UserRole.HR)
    worker = _user(factory, "race.worker@example.com", UserRole.EMPLOYEE)
    with factory() as s:
        department = Department(name="Race Dept")
        s.add(department)
        s.flush()
        s.add(Employee(employee_code="RACE-1", full_name="Race Worker", work_email="race.worker@corp.example.com",
                       department_id=department.id, designation="Engineer", date_of_joining=date(2024, 1, 1),
                       user_id=worker.id))
        s.commit()
        employee_id = s.scalar(select(Employee.id).where(Employee.employee_code == "RACE-1"))
    for _ in range(ROUNDS):
        task = client.post("/api/tasks", headers=_token(hr), json={
            "title": "Race task", "priority": "MEDIUM", "task_type": "FOLLOW_UP", "assignee_employee_id": employee_id,
            "due_date": (date.today() + timedelta(days=3)).isoformat()})
        assert task.status_code == 201, task.text
        task = task.json()
        heads = [_token(hr), _token(hr)]
        codes = _together(2, lambda c, i: c.post(f"/api/tasks/{task['id']}/transition", headers=heads[i], json={
            "action": "start", "expected_version": task["version"]}).status_code)
        # One request writes. The other either loses the version check (409) or, if it loaded the task after the
        # first committed, is the documented idempotent repeat (200, nothing written). Never two writes.
        assert sorted(codes) in ([200, 409], [200, 200]), codes
        with factory() as s:
            assert s.get(Task, task["id"]).version == task["version"] + 1
            assert s.scalar(select(func.count()).select_from(TaskEvent).where(
                TaskEvent.task_id == task["id"], TaskEvent.event_type == TaskEventType.STARTED)) == 1


@pytest.mark.mysql_commits
def test_last_admin_guard_under_forced_lock_interleaving(committed, monkeypatch) -> None:
    """A barrier inside the guard holds the first request (with its row locks) until the second arrives. Observed on
    MySQL 8.0: the second request's guard query blocks on the first one's locked row (both scan users in primary-key
    order), the barrier times out, and the requests are serialized: the first deactivates its target, the second is
    refused as the last administrator (409). Whatever InnoDB does (lock wait or deadlock, which app.main maps to 409),
    an administrator must remain active and the loser must get a 4xx, never a 5xx."""
    from app.api import users as users_api

    factory, client = committed
    a, b = _user(factory, "admin.c@example.com", UserRole.ADMIN), _user(factory, "admin.d@example.com", UserRole.ADMIN)
    original = users_api._keep_an_admin
    barrier = threading.Barrier(2, timeout=2)

    def guarded(db, target):
        original(db, target)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass  # the other request was already refused before reaching the guard

    monkeypatch.setattr(users_api, "_keep_an_admin", guarded)
    outcomes = Counter()
    for _ in range(ROUNDS // 2):
        barrier.reset()
        heads, targets = [_token(a, factory), _token(b, factory)], [b.id, a.id]
        responses = _together(2, lambda c, i: c.patch(f"/api/users/{targets[i]}/status", headers=heads[i],
                                                         json={"is_active": False}))
        codes = sorted(r.status_code for r in responses)
        outcomes[tuple(sorted(f"{r.status_code} {r.json().get('detail', '') if r.status_code != 200 else ''}".strip()
                              for r in responses))] += 1
        assert codes.count(200) <= 1 and all(c < 500 for c in codes), [(r.status_code, r.text) for r in responses]
        with factory() as s:
            assert s.scalar(select(func.count()).select_from(User).where(User.role == UserRole.ADMIN,
                                                                          User.is_active.is_(True))) >= 1
            s.execute(update(User).where(User.id.in_([a.id, b.id])).values(is_active=True))
            s.commit()
    print("forced interleaving outcomes:", dict(outcomes))


@pytest.mark.mysql_commits
def test_concurrent_sign_ins_of_one_account_do_not_deadlock(committed) -> None:
    """The same account signing in from several places at once (tabs, retries). Under load on MySQL 8.0 the sign-in
    transaction used to deadlock (audit-row foreign-key S lock, then the last_login_at X lock); every attempt must
    now succeed."""
    factory, _ = committed
    user = _user(factory, "busy.signer@example.com", UserRole.EMPLOYEE)
    for _ in range(ROUNDS):
        codes = _together(6, lambda c, i: c.post("/api/auth/login", json={"email": user.email,
                                                                           "password": TEST_PASSWORD}).status_code)
        assert codes == [200] * 6, codes
