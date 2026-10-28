import os

# Test-only values, set before the app is imported so tests never depend on a real backend/.env.
# Real environment variables take precedence over .env, so a local .env cannot leak into tests.
TEST_ENV = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "3306",
    "DB_NAME": "workforce_ai_pro_test",
    "DB_USER": "pytest_user",
    "DB_PASSWORD": "pytest-db-password",
    "JWT_SECRET_KEY": "pytest-only-jwt-secret-0123456789-abcdefghijkl",
    "JWT_ALGORITHM": "HS256",
    "JWT_ISSUER": "workforce-ai-pro",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    # A non-UTC zone (UTC+05:30) so date-boundary bugs cannot hide behind UTC.
    "ORG_TIMEZONE": "Asia/Kolkata",
    "ATTENDANCE_LATE_GRACE_MINUTES": "15",
    "ATTENDANCE_DEFAULT_OVERTIME_THRESHOLD_MINUTES": "480",
}
os.environ.update(TEST_ENV)

from collections.abc import Callable, Iterator  # noqa: E402
from datetime import UTC, date, datetime, time  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.exc import IntegrityError, OperationalError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core import rate_limit, security  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import get_db, get_engine, get_session_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Department, Employee, Shift, User, UserRole  # noqa: E402
from tests import mysql_guard  # noqa: E402

TEST_PASSWORD = "Correct-Horse-Battery-9"

_mysql_engine = None  # set by pytest_configure only in the opt-in MySQL mode (tests/mysql_guard.py)


def pytest_configure(config) -> None:
    global _mysql_engine
    config.addinivalue_line("markers", "mysql_only: runs only in the opt-in MySQL mode (tests/mysql_guard.py)")
    config.addinivalue_line("markers", "mysql_commits: commits through separate connections; deletes its own rows")
    config.addinivalue_line("markers", "simulated_concurrent_writer: fakes a second writer with another session on the "
                                       "test database (SQLite only; MySQL covers it in test_mysql_concurrency.py)")
    if mysql_guard.enabled():
        try:
            _mysql_engine = mysql_guard.verified_engine()
        except mysql_guard.NotDisposableError as exc:
            pytest.exit(f"Refusing to run against MySQL: {exc}", returncode=4)


def pytest_unconfigure(config) -> None:
    if _mysql_engine is not None:
        _mysql_engine.dispose()


def pytest_collection_modifyitems(config, items) -> None:
    if _mysql_engine is None:
        skip = pytest.mark.skip(reason="needs the opt-in MySQL mode (WFAI_MYSQL_TEST_URL; see tests/mysql_guard.py)")
        for item in items:
            if "mysql_only" in item.keywords:
                item.add_marker(skip)
    else:
        # In MySQL mode every test shares one connection whose outer transaction is rolled back, so a "second writer"
        # session is nested inside the request's savepoint and is undone with it. Real concurrent writers on separate
        # connections are tested in test_mysql_concurrency.py instead.
        skip = pytest.mark.skip(reason="simulated second writer needs separate connections on MySQL; covered by "
                                       "test_mysql_concurrency.py")
        for item in items:
            if "simulated_concurrent_writer" in item.keywords:
                item.add_marker(skip)


def mysql_mode() -> bool:
    return _mysql_engine is not None


def constraint_violation(kind: str, sqlite_match: str | None = None, mysql_name: str | None = None):
    """pytest.raises for a database constraint violation as each database reports it. kind: "check" or "unique".
    SQLite raises IntegrityError ("CHECK/UNIQUE constraint failed: ..."). MySQL raises IntegrityError 1062 for
    duplicates but, through PyMySQL, OperationalError 3819 for CHECK violations; mysql_name pins the constraint."""
    if _mysql_engine is None:
        return pytest.raises(IntegrityError, match=sqlite_match or f"{kind.upper()} constraint failed")
    if kind == "check":
        return pytest.raises(OperationalError, match=rf"3819, \"Check constraint '{mysql_name or ''}")
    return pytest.raises(IntegrityError, match=rf"1062, \"Duplicate entry .* for key '[a-z_]+\.{mysql_name or ''}")


@pytest.fixture
def mysql_engine():
    """The verified disposable MySQL engine (mysql_only tests)."""
    assert _mysql_engine is not None
    return _mysql_engine


@pytest.fixture(autouse=True)
def _reset_state():
    rate_limit.limiter.clear()  # authentication rate-limit counters are per process; every test starts at zero
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    app.dependency_overrides.clear()
    rate_limit.limiter.clear()


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch):
    # Production uses 12 rounds; tests use the bcrypt minimum so the suite stays fast.
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    security._dummy_hash.cache_clear()


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    """Isolated in-memory SQLite database shared across threads (TestClient runs handlers in a thread).

    In the opt-in MySQL mode: one connection to the verified disposable database inside an outer transaction that is
    rolled back after the test; every session commit becomes a savepoint release, so nothing persists."""
    if _mysql_engine is not None:
        connection = _mysql_engine.connect()
        outer = connection.begin()
        yield sessionmaker(bind=connection, autoflush=False, expire_on_commit=False,
                           join_transaction_mode="create_savepoint")
        outer.rollback()
        connection.close()
        return
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory) -> TestClient:
    def _override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


@pytest.fixture
def make_user(db) -> Callable[..., User]:
    def _make_user(
        email: str = "alice@example.com",
        password: str = TEST_PASSWORD,
        role: UserRole = UserRole.EMPLOYEE,
        is_active: bool = True,
        full_name: str = "Alice Example",
    ) -> User:
        user = User(
            full_name=full_name,
            email=email,
            password_hash=security.hash_password(password),
            role=role,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make_user


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def headers_for(make_user) -> Callable[..., dict[str, str]]:
    """Create a user with the given role and return Authorization headers for them."""

    def _headers_for(role: UserRole, email: str | None = None) -> dict[str, str]:
        user = make_user(email=email or f"{role.value.lower()}@example.com", role=role)
        return auth_header(security.create_access_token(user.id).token)

    return _headers_for


@pytest.fixture
def make_department(db) -> Callable[..., Department]:
    def _make_department(name: str = "Engineering", is_active: bool = True) -> Department:
        department = Department(name=name, is_active=is_active)
        db.add(department)
        db.commit()
        return department

    return _make_department


@pytest.fixture
def make_employee(db) -> Callable[..., Employee]:
    counter = iter(range(1, 10_000))

    def _make_employee(department: Department, **overrides) -> Employee:
        n = next(counter)
        values = {
            "employee_code": f"EMP{n:04d}",
            "full_name": f"Employee {n}",
            "work_email": f"employee{n}@corp.example.com",
            "department_id": department.id,
            "designation": "Engineer",
            "date_of_joining": date(2024, 1, 1),
        }
        values.update(overrides)
        employee = Employee(**values)
        db.add(employee)
        db.commit()
        return employee

    return _make_employee


@pytest.fixture
def count_queries(session_factory) -> Callable[[], list[str]]:
    """Returns a function that yields the SQL statements executed so far on the test engine."""
    statements: list[str] = []
    engine = session_factory.kw["bind"]

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    yield lambda: statements
    event.remove(engine, "before_cursor_execute", _record)


ORG_TZ = ZoneInfo(TEST_ENV["ORG_TIMEZONE"])


def local(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """An organization-local wall-clock time, returned as an aware UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=ORG_TZ).astimezone(UTC)


class Clock:
    def __init__(self) -> None:
        self.now = local(2026, 3, 10, 9, 0)

    def set(self, value: datetime) -> None:
        self.now = value

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    """Controls the server time used by the attendance endpoints."""
    from app.api import attendance

    fake = Clock()
    monkeypatch.setattr(attendance, "utcnow", fake)
    return fake


@pytest.fixture
def make_shift(db) -> Callable[..., Shift]:
    def _make_shift(
        name: str = "General",
        start: time = time(9, 0),
        end: time = time(18, 0),
        break_minutes: int = 60,
        overtime_threshold_minutes: int = 480,
        is_active: bool = True,
    ) -> Shift:
        shift = Shift(
            name=name, start_time=start, end_time=end, break_minutes=break_minutes,
            overtime_threshold_minutes=overtime_threshold_minutes, is_active=is_active,
        )
        db.add(shift)
        db.commit()
        return shift

    return _make_shift


@pytest.fixture
def staff(make_user, make_department, make_employee):
    """Create a login linked to an employee record. Returns (employee, headers)."""
    departments: dict[str, Department] = {}
    counter = iter(range(1, 10_000))

    def _staff(
        role: UserRole = UserRole.EMPLOYEE, *, email: str | None = None, department: str = "Engineering", **employee_fields
    ) -> tuple[Employee, dict[str, str]]:
        if department not in departments:
            departments[department] = make_department(department)
        user = make_user(email=email or f"staff{next(counter)}.{role.value.lower()}@example.com", role=role)
        employee = make_employee(departments[department], user_id=user.id, **employee_fields)
        return employee, auth_header(security.create_access_token(user.id).token)

    return _staff


@pytest.fixture
def leave_clock(monkeypatch) -> Clock:
    """Controls server time for the leave endpoints. Default: Monday 2026-03-02 10:00 org time."""
    from app.api import leave

    fake = Clock()
    fake.set(local(2026, 3, 2, 10, 0))
    monkeypatch.setattr(leave, "utcnow", fake)
    return fake


@pytest.fixture
def make_leave_type(db):
    from app.models import LeaveType

    def _make_leave_type(code: str = "ANNUAL", name: str | None = None, **fields) -> "LeaveType":
        leave_type = LeaveType(code=code, name=name or code.title(), **fields)
        db.add(leave_type)
        db.commit()
        return leave_type

    return _make_leave_type


@pytest.fixture
def make_policy(db):
    from app.models import LeavePolicy

    def _make_policy(leave_type, *, department=None, annual_allowance_days: int = 10,
                     effective_from: date = date(2026, 1, 1), **fields) -> "LeavePolicy":
        policy = LeavePolicy(
            leave_type_id=leave_type.id, department_id=department.id if department else None,
            annual_allowance_days=annual_allowance_days, effective_from=effective_from, **fields,
        )
        db.add(policy)
        db.commit()
        return policy

    return _make_policy


@pytest.fixture
def make_component(db):
    from app.models import CalculationMethod, SalaryComponent, SalaryComponentKind

    def _make_component(code: str, kind=SalaryComponentKind.EARNING, method=CalculationMethod.FIXED,
                        display_order: int = 100, **fields) -> "SalaryComponent":
        component = SalaryComponent(code=code, name=fields.pop("name", code.replace("_", " ").title()),
                                    kind=kind, calculation_method=method, display_order=display_order, **fields)
        db.add(component)
        db.commit()
        return component

    return _make_component


@pytest.fixture
def make_profile(db):
    from decimal import Decimal

    from app.models import PayrollProfile, PayrollProfileComponent

    def _make_profile(employee, base_salary="50000.00", *, effective_from: date = date(2026, 1, 1),
                      components: tuple = (), **fields) -> "PayrollProfile":
        profile = PayrollProfile(employee_id=employee.id, base_salary=Decimal(base_salary),
                                 effective_from=effective_from, **fields)
        profile.components = [
            PayrollProfileComponent(component_id=component.id,
                                    amount=Decimal(value) if component.calculation_method.value == "FIXED" else None,
                                    percentage=None if component.calculation_method.value == "FIXED" else Decimal(value))
            for component, value in components
        ]
        db.add(profile)
        db.commit()
        return profile

    return _make_profile


@pytest.fixture
def ml_dirs(tmp_path, monkeypatch):
    """Isolated artifact and dataset directories (never the real backend/ml folders)."""
    from app.services import attrition as attrition_service

    artifact_dir, dataset_dir = tmp_path / "models", tmp_path / "datasets"
    dataset_dir.mkdir()
    monkeypatch.setenv("ML_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.setenv("ML_DATASET_DIR", str(dataset_dir))
    get_settings.cache_clear()
    attrition_service.clear_cache()
    yield {"artifacts": artifact_dir, "datasets": dataset_dir}
    attrition_service.clear_cache()
