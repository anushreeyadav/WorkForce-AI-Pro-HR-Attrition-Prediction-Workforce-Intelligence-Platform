"""Sub-prompt 32 hardening: readiness check, API Content-Security-Policy, MySQL error mapping, MySQL aggregate types,
and self-service password change. Synthetic accounts only; no real database."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pymysql
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.api import health
from app.core import security
from app.core.config import get_settings
from app.main import create_app
from app.models import AuditLog, User, UserRole
from app.services.attrition import extract_features
from tests.conftest import TEST_PASSWORD, auth_header, mysql_mode

NEW_PASSWORD = "Another-Long-Passphrase-42"


# --- Readiness ------------------------------------------------------------------------------------


def test_readiness_requires_the_migration_head(client, db) -> None:
    if mysql_mode():
        pytest.skip("creates and edits alembic_version (DDL); MySQL readiness: test_mysql_concurrency.py")
    # The test database is created from the models, without Alembic: not migrated.
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == {"status": "not_ready", "database": "reachable", "schema": "not_migrated"}

    db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db.execute(text("INSERT INTO alembic_version VALUES ('0000aaaa0000')"))
    db.commit()
    mismatch = client.get("/api/health/ready")
    assert mismatch.status_code == 503 and mismatch.json()["detail"]["schema"] == "revision_mismatch"
    assert "0000aaaa0000" not in mismatch.text and health.expected_schema_revision() not in mismatch.text

    db.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": health.expected_schema_revision()})
    db.commit()
    assert client.get("/api/health/ready").json() == {"status": "ready", "database": "reachable", "schema": "current"}
    assert client.get("/api/health").json() == {"status": "ok"}  # liveness never touches the database


def test_readiness_reports_an_unreachable_database() -> None:
    from app.core.database import get_db

    class Down:
        def execute(self, *_):
            raise OperationalError("SELECT 1", {}, Exception("down"))

        def rollback(self):
            pass

    app = create_app(get_settings())
    app.dependency_overrides[get_db] = lambda: Down()
    response = TestClient(app).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == {"status": "not_ready", "database": "unavailable", "schema": "unknown"}


# --- Headers --------------------------------------------------------------------------------------


def test_api_responses_forbid_active_content(client) -> None:
    response = client.get("/api/health")
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    docs = client.get("/docs")  # Swagger UI (development only) needs its own scripts: no API policy applied
    assert docs.status_code == 200 and "content-security-policy" not in docs.headers


# --- MySQL error mapping --------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "status", "detail"), [
    (1213, 409, "The request conflicted with a concurrent change. Please retry."),  # deadlock
    (1205, 409, "The request conflicted with a concurrent change. Please retry."),  # lock wait timeout
    (3819, 500, "Internal Server Error"),  # CHECK constraint (PyMySQL raises OperationalError)
    (2013, 503, "Database unavailable"),  # lost connection
])
def test_mysql_operational_errors_are_mapped_by_cause(code, status, detail, caplog) -> None:
    app = create_app(get_settings())
    router = APIRouter()

    @router.get("/api/boom")
    def boom():
        raise OperationalError("UPDATE t", {}, pymysql.err.OperationalError(code, "secret-host user=root detail"))

    app.include_router(router)
    response = TestClient(app, raise_server_exceptions=False).get("/api/boom")
    assert (response.status_code, response.json()) == (status, {"detail": detail})
    assert "secret-host" not in caplog.text and "root" not in caplog.text


def test_features_are_plain_json_types_when_the_database_returns_decimals() -> None:
    """MySQL returns SUM() and AVG() as Decimal; the feature dict is stored in a JSON column."""
    employee = SimpleNamespace(id=7, department_id=3, date_of_joining=__import__("datetime").date(2024, 1, 1))
    rows = [(7, Decimal(40), Decimal(36), Decimal(9), Decimal(2), Decimal("12.5000"))]
    db = SimpleNamespace(execute=lambda _stmt: SimpleNamespace(all=lambda: rows))
    features = extract_features(db, [employee], __import__("datetime").date(2026, 3, 1))[7]
    assert features["late_rate_90d"] == 0.25 and type(features["late_rate_90d"]) is float
    assert json.loads(json.dumps(features)) == features


# --- Password change ------------------------------------------------------------------------------


@pytest.fixture
def account(client, make_user):
    user = make_user(email="changer@example.com", role=UserRole.EMPLOYEE)
    token = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD}).json()["access_token"]
    other = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD}).json()["access_token"]
    return user, auth_header(token), auth_header(other)


def change(client, headers, current=TEST_PASSWORD, new=NEW_PASSWORD):
    return client.post("/api/auth/change-password", headers=headers,
                       json={"current_password": current, "new_password": new})


def test_password_change_ends_every_session_and_takes_effect(client, db, account) -> None:
    user, headers, other = account
    response = change(client, headers)
    assert response.status_code == 200 and "Every session has ended" in response.json()["detail"]
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=other).status_code == 401
    login = lambda pw: client.post("/api/auth/login", json={"email": user.email, "password": pw}).status_code  # noqa: E731
    assert (login(TEST_PASSWORD), login(NEW_PASSWORD)) == (401, 200)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "auth.password_changed"))
    assert log.entity_id == str(user.id) and not log.details
    stored = db.scalars(select(AuditLog.details)).all()
    assert all(TEST_PASSWORD not in json.dumps(d) and NEW_PASSWORD not in json.dumps(d) for d in stored)


def test_wrong_current_password_is_403_not_401_and_rate_limited(client, db, account) -> None:
    user, headers, _ = account
    for _ in range(5):  # AUTH_LOGIN_MAX_FAILURES default
        response = change(client, headers, current="Not-The-Password-1")
        assert (response.status_code, response.json()["detail"]) == (403, "Current password is incorrect")
    limited = change(client, headers)  # even the right password is refused while limited (no oracle)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert client.get("/api/auth/me", headers=headers).status_code == 200  # the session itself is untouched
    db.expire_all()
    assert security.verify_password(TEST_PASSWORD, db.get(User, user.id).password_hash)
    reasons = db.scalars(select(AuditLog.details).where(AuditLog.action == "auth.password_change_failed")).all()
    assert reasons == [{"reason": "invalid_current_password"}] * 5


@pytest.mark.parametrize(("new", "status"), [("Qz7!kp", 422), (TEST_PASSWORD, 422), ("x" * 73, 422)])
def test_new_password_rules(client, account, new, status) -> None:
    _, headers, _ = account
    response = change(client, headers, new=new)
    assert response.status_code == status
    assert new not in response.text  # validation errors never echo the submitted password


def test_password_change_requires_authentication_and_own_account_only(client, account) -> None:
    assert change(client, {}).status_code == 401
    _, headers, _ = account
    extra = client.post("/api/auth/change-password", headers=headers,
                        json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "user_id": 1})
    assert extra.status_code == 422  # no way to name another account
