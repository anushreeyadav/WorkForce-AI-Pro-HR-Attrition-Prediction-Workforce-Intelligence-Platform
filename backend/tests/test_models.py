import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session, configure_mappers

from app.models import AuditLog, Base, Department, Employee, EmploymentStatus, User, UserRole

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def session():
    """Isolated in-memory SQLite database; never touches MySQL."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def make_department(name: str = "Engineering") -> Department:
    return Department(name=name)


def make_user(email: str = "alice@example.com", **kwargs) -> User:
    return User(full_name="Alice", email=email, password_hash="$2b$12$fakehashvalue", **kwargs)


def make_employee(department: Department, code: str = "EMP001", email: str = "alice@corp.com", **kwargs):
    return Employee(
        employee_code=code,
        full_name="Alice",
        work_email=email,
        department=department,
        designation="Engineer",
        date_of_joining=date(2026, 1, 15),
        **kwargs,
    )


# --- Metadata / structure -------------------------------------------------------------------------


def test_all_models_registered_and_mappers_configure() -> None:
    configure_mappers()
    assert set(Base.metadata.tables) == {
        "users", "departments", "employees", "audit_logs", "shifts", "attendance_records",
        "leave_types", "leave_policies", "leave_requests", "leave_balances", "notifications",
        "salary_components", "payroll_profiles", "payroll_profile_components", "payroll_periods",
        "payroll_records", "payroll_line_items",
        "attrition_model_versions", "attrition_predictions",
        "datasets", "dataset_versions",
        "risk_alerts", "risk_events",
        "workforce_snapshots", "workforce_snapshot_departments",
        "interventions", "intervention_events",
        "recommendations",
        "risk_alert_escalations",
        "tasks", "task_events",
        "performance_cycles", "performance_goals", "performance_reviews", "performance_events",
        "revoked_tokens",
    }


@pytest.mark.parametrize(
    "table, columns",
    [
        ("users", {"id", "full_name", "email", "password_hash", "role", "is_active",
                   "last_login_at", "token_version", "created_at", "updated_at"}),
        ("revoked_tokens", {"jti", "user_id", "expires_at", "revoked_at"}),
        ("departments", {"id", "name", "description", "is_active", "created_at", "updated_at"}),
        ("employees", {"id", "employee_code", "user_id", "full_name", "work_email", "department_id",
                       "designation", "date_of_joining", "status", "phone", "created_at", "updated_at"}),
        ("audit_logs", {"id", "actor_user_id", "action", "entity_type", "entity_id", "metadata",
                        "created_at"}),
    ],
)
def test_expected_columns(table: str, columns: set[str]) -> None:
    assert set(Base.metadata.tables[table].columns.keys()) == columns


def test_no_plaintext_password_or_payroll_columns() -> None:
    assert "password" not in Base.metadata.tables["users"].columns
    employee_columns = set(Base.metadata.tables["employees"].columns.keys())
    assert not employee_columns & {"salary", "base_salary", "net_pay", "password", "password_hash", "role", "is_active"}


def test_nullability() -> None:
    employees = Base.metadata.tables["employees"].columns
    assert employees["user_id"].nullable
    assert employees["phone"].nullable
    assert not employees["department_id"].nullable
    assert Base.metadata.tables["users"].columns["last_login_at"].nullable
    assert Base.metadata.tables["audit_logs"].columns["actor_user_id"].nullable


def _unique_constraints(table: str) -> dict[str, tuple[str, ...]]:
    from sqlalchemy import UniqueConstraint

    return {
        c.name: tuple(col.name for col in c.columns)
        for c in Base.metadata.tables[table].constraints
        if isinstance(c, UniqueConstraint)
    }


def test_unique_constraints() -> None:
    assert _unique_constraints("users") == {"uq_users_email": ("email",)}
    assert _unique_constraints("departments") == {"uq_departments_name": ("name",)}
    assert _unique_constraints("employees") == {
        "uq_employees_employee_code": ("employee_code",),
        "uq_employees_work_email": ("work_email",),
        "uq_employees_user_id": ("user_id",),
    }


def test_indexes() -> None:
    def indexes(table: str) -> dict[str, tuple[str, ...]]:
        return {i.name: tuple(c.name for c in i.columns) for i in Base.metadata.tables[table].indexes}

    assert indexes("users") == {"ix_users_role": ("role",)}
    assert indexes("employees") == {
        "ix_employees_department_id": ("department_id",),
        "ix_employees_status": ("status",),
        "ix_employees_created_at": ("created_at",),
    }
    assert indexes("audit_logs") == {
        "ix_audit_logs_action": ("action",),
        "ix_audit_logs_action_created_at": ("action", "created_at"),
        "ix_audit_logs_actor_user_id": ("actor_user_id",),
        "ix_audit_logs_created_at": ("created_at",),
        "ix_audit_logs_entity": ("entity_type", "entity_id"),
    }


def test_foreign_keys() -> None:
    def fks(table: str) -> dict[str, tuple[str, str | None]]:
        return {
            fk.parent.name: (fk.target_fullname, fk.ondelete)
            for fk in Base.metadata.tables[table].foreign_keys
        }

    assert fks("employees") == {
        "user_id": ("users.id", "SET NULL"),
        "department_id": ("departments.id", "RESTRICT"),
    }
    assert fks("audit_logs") == {"actor_user_id": ("users.id", "SET NULL")}


def test_relationships() -> None:
    user_rels = inspect(User).relationships
    employee_rels = inspect(Employee).relationships
    department_rels = inspect(Department).relationships

    assert not user_rels["employee"].uselist
    assert user_rels["employee"].back_populates == "user"
    assert employee_rels["user"].mapper.class_ is User
    assert employee_rels["department"].mapper.class_ is Department
    assert department_rels["employees"].uselist
    assert department_rels["employees"].back_populates == "department"


def test_enum_values() -> None:
    assert [r.value for r in UserRole] == ["ADMIN", "HR", "MANAGER", "EMPLOYEE"]
    assert [s.value for s in EmploymentStatus] == ["ACTIVE", "INACTIVE", "ON_LEAVE"]
    assert Base.metadata.tables["users"].columns["role"].type.enums == ["ADMIN", "HR", "MANAGER", "EMPLOYEE"]
    assert Base.metadata.tables["employees"].columns["status"].type.enums == ["ACTIVE", "INACTIVE", "ON_LEAVE"]


# --- Behaviour on an isolated SQLite database -------------------------------------------------------


def test_defaults_and_utc_timestamps(session: Session) -> None:
    department = make_department()
    user = make_user()
    employee = make_employee(department, user=user)
    session.add_all([department, user, employee])
    session.commit()
    session.expire_all()

    assert user.role is UserRole.EMPLOYEE
    assert user.is_active is True
    assert department.is_active is True
    assert employee.status is EmploymentStatus.ACTIVE
    assert user.last_login_at is None
    for value in (user.created_at, user.updated_at, employee.created_at, department.updated_at):
        assert value.tzinfo is UTC
        assert abs(datetime.now(UTC) - value) < timedelta(minutes=1)
    assert user.employee is employee
    assert department.employees == [employee]


def test_non_utc_aware_datetime_is_converted(session: Session) -> None:
    user = make_user(last_login_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))))
    session.add(user)
    session.commit()
    session.expire_all()
    assert user.last_login_at == datetime(2026, 5, 1, 6, 30, tzinfo=UTC)


def test_naive_datetime_rejected(session: Session) -> None:
    session.add(make_user(last_login_at=datetime(2026, 5, 1, 12, 0)))
    with pytest.raises(StatementError, match="Naive datetimes are not allowed"):
        session.commit()


def test_emails_normalized_to_lowercase() -> None:
    assert make_user(email="  Alice@Example.COM ").email == "alice@example.com"
    assert make_employee(make_department(), email="Bob@Corp.COM").work_email == "bob@corp.com"


def test_duplicate_email_rejected_case_insensitively(session: Session) -> None:
    session.add(make_user(email="alice@example.com"))
    session.commit()
    session.add(make_user(email="ALICE@example.com"))
    with pytest.raises(IntegrityError, match="users.email"):
        session.commit()


def test_duplicate_employee_code_rejected(session: Session) -> None:
    department = make_department()
    session.add_all([department, make_employee(department, code="EMP001", email="a@corp.com")])
    session.commit()
    session.add(make_employee(department, code="EMP001", email="b@corp.com"))
    with pytest.raises(IntegrityError, match="employees.employee_code"):
        session.commit()


def test_user_linked_to_at_most_one_employee(session: Session) -> None:
    department = make_department()
    user = make_user()
    session.add_all([department, user, make_employee(department, code="E1", email="a@corp.com", user=user)])
    session.commit()
    session.add(make_employee(department, code="E2", email="b@corp.com", user_id=user.id))
    with pytest.raises(IntegrityError, match="employees.user_id"):
        session.commit()


def test_employee_requires_existing_department(session: Session) -> None:
    session.add(
        Employee(employee_code="E1", full_name="X", work_email="x@corp.com", department_id=999,
                 designation="Y", date_of_joining=date(2026, 1, 1))
    )
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        session.commit()


def test_audit_log_accepts_safe_metadata(session: Session) -> None:
    log = AuditLog(action="employee.created", entity_type="employee", entity_id="42",
                   details={"fields": ["full_name", "designation"], "source": "ui"})
    session.add(log)
    session.commit()
    session.expire_all()
    assert log.details == {"fields": ["full_name", "designation"], "source": "ui"}
    assert log.created_at.tzinfo is UTC


@pytest.mark.parametrize(
    "details",
    [
        {"password": "x"},
        {"new_Password": "x"},
        {"changes": {"jwt_token": "x"}},
        {"items": [{"api_key": "x"}]},
        {"Authorization": "Bearer x"},
        {"password_hash": "x"},
    ],
)
def test_audit_log_rejects_sensitive_metadata(details: dict) -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        AuditLog(action="user.updated", entity_type="user", details=details)


def test_user_repr_has_no_password_hash() -> None:
    assert "fakehash" not in repr(make_user())


# --- Import side effects ----------------------------------------------------------------------------


def test_importing_app_and_models_creates_no_tables_or_connections() -> None:
    """Run in a fresh interpreter so module-level side effects are observable."""
    code = (
        "import sqlalchemy\n"
        "def boom(*a, **k): raise SystemExit('create_all called at import time')\n"
        "sqlalchemy.MetaData.create_all = boom\n"
        "import app.main, app.models\n"
        "from app.core.database import get_engine\n"
        "assert get_engine.cache_info().currsize == 0, 'engine created at import time'\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_DIR, env=os.environ.copy(),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_health_endpoints_unchanged() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    assert {"/api/health", "/api/health/db"} <= set(paths)
    assert set(paths["/api/health"]) == {"get"}
    assert set(paths["/api/health/db"]) == {"get"}
    # Full route inventory: any new route must be added here deliberately.
    assert set(paths) - {"/api/health", "/api/health/db"} == {
        "/api/auth/register", "/api/auth/login", "/api/auth/me", "/api/auth/logout",
        "/api/auth/change-password", "/api/health/ready",  # Sub-prompt 32
        "/api/departments", "/api/departments/{department_id}",
        "/api/departments/{department_id}/employees",
        "/api/employees", "/api/employees/search", "/api/employees/{employee_id}",
        "/api/employees/{employee_id}/status",
        "/api/shifts", "/api/shifts/{shift_id}", "/api/shifts/{shift_id}/status",
        "/api/attendance", "/api/attendance/check-in", "/api/attendance/check-out",
        "/api/attendance/me", "/api/attendance/me/summary", "/api/attendance/summary",
        "/api/attendance/{attendance_id}", "/api/attendance/{attendance_id}/correction",
        "/api/leave/types", "/api/leave/types/{leave_type_id}", "/api/leave/policies",
        "/api/leave/policies/{policy_id}", "/api/leave/requests", "/api/leave/requests/me",
        "/api/leave/requests/{request_id}", "/api/leave/requests/{request_id}/approve",
        "/api/leave/requests/{request_id}/reject", "/api/leave/requests/{request_id}/cancel",
        "/api/leave/balances/me", "/api/leave/conflicts",
        "/api/notifications/me", "/api/notifications/{notification_id}/read",
        "/api/payroll/components", "/api/payroll/components/{component_id}", "/api/payroll/profiles",
        "/api/payroll/profiles/{profile_id}", "/api/payroll/periods", "/api/payroll/periods/{period_id}",
        "/api/payroll/periods/{period_id}/calculate", "/api/payroll/periods/{period_id}/records",
        "/api/payroll/records/{record_id}", "/api/payroll/records/{record_id}/approve",
        "/api/payroll/records/{record_id}/mark-paid", "/api/payroll/me", "/api/payroll/me/{record_id}/payslip",
        "/api/ai/attrition/predict/{employee_id}", "/api/ai/attrition/predict-batch",
        "/api/ai/attrition/history/{employee_id}", "/api/ai/attrition/summary", "/api/ai/attrition/model-status",
        "/api/ai/attrition/train", "/api/ai/attrition/models/{model_version}/activate",
        "/api/datasets", "/api/datasets/{dataset_id}", "/api/datasets/{dataset_id}/versions",
        "/api/datasets/{dataset_id}/validate", "/api/datasets/{dataset_id}/validation",
        "/api/datasets/{dataset_id}/archive", "/api/datasets/{dataset_id}/download",
        "/api/risk-monitoring/run", "/api/risk-monitoring/alerts", "/api/risk-monitoring/alerts/{alert_id}",
        "/api/risk-monitoring/alerts/{alert_id}/acknowledge", "/api/risk-monitoring/alerts/{alert_id}/resolve",
        "/api/risk-monitoring/alerts/{alert_id}/assign", "/api/risk-monitoring/employees/{employee_id}/history",
        "/api/risk-monitoring/summary",
        "/api/workforce-analytics/snapshots/capture", "/api/workforce-analytics/snapshots",
        "/api/workforce-analytics/overview", "/api/workforce-analytics/trends", "/api/workforce-analytics/forecast",
        "/api/workforce-analytics/stability", "/api/workforce-analytics/retention",
        "/api/workforce-analytics/departments/{department_id}",
        "/api/interventions", "/api/interventions/categories", "/api/interventions/summary",
        "/api/interventions/{intervention_id}", "/api/interventions/{intervention_id}/assign",
        "/api/interventions/{intervention_id}/start", "/api/interventions/{intervention_id}/complete",
        "/api/interventions/{intervention_id}/cancel", "/api/interventions/{intervention_id}/notes",
        "/api/interventions/{intervention_id}/history",
        "/api/recommendations", "/api/recommendations/summary", "/api/recommendations/evaluate",
        "/api/recommendations/{recommendation_id}", "/api/recommendations/{recommendation_id}/accept",
        "/api/recommendations/{recommendation_id}/dismiss", "/api/recommendations/{recommendation_id}/complete",
        "/api/recommendations/{recommendation_id}/create-intervention",
        "/api/risk-monitoring/escalations/evaluate",
        "/api/workforce-activity/feed", "/api/workforce-activity/summary", "/api/workforce-activity/signals",
        "/api/workforce-activity/sources", "/api/workforce-activity/departments/{department_id}",
        "/api/workforce-activity/updates", "/api/workforce-activity/status",
        "/api/reports/catalog", "/api/reports/employees", "/api/reports/attendance", "/api/reports/leave",
        "/api/reports/payroll", "/api/reports/attrition", "/api/reports/workforce", "/api/reports/activity",
        "/api/audit-logs", "/api/audit-logs/catalog", "/api/audit-logs/export",
        "/api/users", "/api/users/{user_id}", "/api/users/{user_id}/status", "/api/users/{user_id}/role",
        "/api/users/{user_id}/revoke-sessions",
        "/api/tasks", "/api/tasks/summary", "/api/tasks/{task_id}", "/api/tasks/{task_id}/assign",
        "/api/tasks/{task_id}/transition", "/api/tasks/{task_id}/history",
        "/api/performance/summary", "/api/performance/cycles", "/api/performance/cycles/{cycle_id}",
        "/api/performance/cycles/{cycle_id}/transition", "/api/performance/goals",
        "/api/performance/goals/{goal_id}", "/api/performance/goals/{goal_id}/history",
        "/api/performance/reviews", "/api/performance/reviews/{review_id}",
        "/api/performance/reviews/{review_id}/history", "/api/performance/reviews/{review_id}/submit-self-assessment",
        "/api/performance/reviews/{review_id}/submit-manager-review", "/api/performance/reviews/{review_id}/finalize",
        "/api/performance/reviews/{review_id}/reopen", "/api/performance/reviews/{review_id}/cancel",
        "/api/recommendations/{recommendation_id}/history", "/api/recommendations/{recommendation_id}/link-task",
        "/api/search",
    }
