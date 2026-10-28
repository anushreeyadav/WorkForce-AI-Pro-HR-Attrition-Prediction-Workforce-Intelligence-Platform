"""Token revocation, account lifecycle, authentication rate limits and deployment configuration (Sub-prompt 31).

Synthetic users in in-memory SQLite; test-only placeholder settings. No real database, credentials or network.
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import rate_limit, security
from app.core.config import ConfigurationError, Settings, get_settings
from app.main import create_app
from app.models import AuditLog, EmploymentStatus, RevokedToken, User, UserRole
from tests.conftest import TEST_ENV, TEST_PASSWORD, auth_header

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE


def login(client, email, password=TEST_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def token_for(client, email) -> dict:
    response = login(client, email)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["access_token"])


def me(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).status_code


# --- Token revocation ---------------------------------------------------------------------------------------------------


def test_logout_revokes_only_that_token_and_records_no_token_data(client, db, make_user) -> None:
    user = make_user(email="worker@example.com")
    first, second = token_for(client, "worker@example.com"), token_for(client, "worker@example.com")
    assert client.post("/api/auth/logout", headers=first).status_code == 200
    assert me(client, first) == 401 and me(client, second) == 200
    row = db.scalar(select(RevokedToken))
    assert row.user_id == user.id and len(row.jti) == 32
    raw = first["Authorization"].split()[1]
    audit = " ".join(f"{r.action} {r.details}" for r in db.scalars(select(AuditLog)))
    assert "auth.logout" in audit and raw not in audit and row.jti not in audit


def test_expired_revocation_rows_are_cleaned_up_on_logout(client, db, make_user) -> None:
    user = make_user(email="worker@example.com")
    db.add(RevokedToken(jti="old" * 10, user_id=user.id, expires_at=datetime.now(UTC) - timedelta(hours=1),
                        revoked_at=datetime.now(UTC) - timedelta(hours=2)))
    db.commit()
    client.post("/api/auth/logout", headers=token_for(client, "worker@example.com"))
    db.expire_all()
    assert [r.jti for r in db.scalars(select(RevokedToken))] != [] and "old" * 10 not in [r.jti for r in db.scalars(select(RevokedToken))]


def test_tokens_issued_before_session_versions_still_work_until_changed(client, db, make_user) -> None:
    """Tokens from before the upgrade have no "ver" claim: read as version 0, the default of existing accounts."""
    user = make_user(email="legacy@example.com")
    settings = get_settings()
    now = datetime.now(UTC)
    legacy = jwt.encode({"sub": str(user.id), "iss": settings.jwt_issuer, "iat": now, "nbf": now,
                         "exp": now + timedelta(minutes=5), "jti": "legacy-token-id", "type": "access"},
                        settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)
    assert me(client, auth_header(legacy)) == 200
    user.token_version = 1
    db.commit()
    assert me(client, auth_header(legacy)) == 401


@pytest.mark.parametrize("ver", [-1, "1", True, 1.5])
def test_malformed_session_version_claims_are_rejected(make_user, ver) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode({"sub": "1", "iss": settings.jwt_issuer, "iat": now, "exp": now + timedelta(minutes=5),
                        "jti": "x", "type": "access", "ver": ver}, settings.jwt_secret_key.get_secret_value(),
                       algorithm=settings.jwt_algorithm)
    with pytest.raises(security.InvalidTokenError):
        security.decode_access_token(token)


def test_revocation_check_fails_closed_when_the_database_is_unavailable(client, make_user) -> None:
    from sqlalchemy.exc import OperationalError

    from app.core.database import get_db
    from app.main import app

    make_user(email="worker@example.com")
    headers = token_for(client, "worker@example.com")

    class Down:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT", {}, Exception("(2003) Can't connect to MySQL server on 'db-host'"))

    app.dependency_overrides[get_db] = lambda: Down()
    response = TestClient(app).get("/api/auth/me", headers=headers)
    assert response.status_code == 503 and response.json() == {"detail": "Database unavailable"}
    assert "db-host" not in response.text


# --- Account administration --------------------------------------------------------------------------------------------


@pytest.fixture
def accounts(client, make_user, staff):
    admin = make_user(email="admin@example.com", role=ADMIN)
    make_user(email="admin2@example.com", role=ADMIN)
    hr_emp, _ = staff(HR, email="hr@example.com", department="People")
    worker = make_user(email="worker@example.com")
    return {"admin": admin, "admin_h": token_for(client, "admin@example.com"), "hr_h": token_for(client, "hr@example.com"),
            "hr_emp": hr_emp, "worker": worker}


def test_only_admins_administer_accounts(client, accounts, staff) -> None:
    _, manager_h = staff(MANAGER, department="Engineering")
    _, employee_h = staff(EMPLOYEE, department="Engineering")
    target = accounts["worker"].id
    for headers in (accounts["hr_h"], manager_h, employee_h):
        assert client.get("/api/users", headers=headers).status_code == 403
        assert client.patch(f"/api/users/{target}/role", headers=headers, json={"role": "ADMIN"}).status_code == 403
        assert client.patch(f"/api/users/{target}/status", headers=headers, json={"is_active": False}).status_code == 403
        assert client.post(f"/api/users/{target}/revoke-sessions", headers=headers).status_code == 403
    listing = client.get("/api/users", headers=accounts["admin_h"], params={"role": "HR"}).json()
    assert [u["email"] for u in listing["items"]] == ["hr@example.com"]
    assert listing["items"][0]["employee_id"] == accounts["hr_emp"].id
    assert "password_hash" not in str(listing) and "token_version" not in str(listing)


def test_deactivation_ends_sessions_and_reactivation_does_not_revive_them(client, db, accounts) -> None:
    worker = token_for(client, "worker@example.com")
    uid = accounts["worker"].id
    off = client.patch(f"/api/users/{uid}/status", headers=accounts["admin_h"], json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert me(client, worker) == 401
    assert login(client, "worker@example.com").status_code == 403
    assert client.patch(f"/api/users/{uid}/status", headers=accounts["admin_h"], json={"is_active": True}).status_code == 200
    assert me(client, worker) == 401  # the old token stays dead
    assert me(client, token_for(client, "worker@example.com")) == 200
    actions = [r.action for r in db.scalars(select(AuditLog).where(AuditLog.entity_id == str(uid)))]
    assert "user.deactivated" in actions and "user.reactivated" in actions


def test_role_changes_end_sessions_and_new_role_applies_after_sign_in(client, accounts) -> None:
    worker = token_for(client, "worker@example.com")
    uid = accounts["worker"].id
    changed = client.patch(f"/api/users/{uid}/role", headers=accounts["admin_h"], json={"role": "HR"})
    assert changed.status_code == 200 and changed.json()["role"] == "HR"
    assert me(client, worker) == 401
    fresh = token_for(client, "worker@example.com")
    assert client.get("/api/payroll/periods", headers=fresh).status_code == 200


def test_no_self_changes_and_the_last_admin_stays(client, db, accounts) -> None:
    admin = accounts["admin"]
    h = accounts["admin_h"]
    assert client.patch(f"/api/users/{admin.id}/role", headers=h, json={"role": "EMPLOYEE"}).status_code == 403
    assert client.patch(f"/api/users/{admin.id}/status", headers=h, json={"is_active": False}).status_code == 403
    other = db.scalar(select(User).where(User.email == "admin2@example.com"))
    assert client.patch(f"/api/users/{other.id}/status", headers=h, json={"is_active": False}).status_code == 200
    # admin2 is gone; "admin" is now the last active ADMIN. Promote and demote others freely, never the last one.
    worker = accounts["worker"].id
    assert client.patch(f"/api/users/{worker}/role", headers=h, json={"role": "ADMIN"}).status_code == 200
    promoted = token_for(client, "worker@example.com")
    assert client.patch(f"/api/users/{admin.id}/role", headers=promoted, json={"role": "HR"}).status_code == 200
    # Now "worker" is the only active ADMIN; nobody may demote or deactivate them, and they cannot change themselves.
    assert client.patch(f"/api/users/{worker}/role", headers=promoted, json={"role": "EMPLOYEE"}).status_code == 403
    db.expire_all()
    assert db.scalar(select(User).where(User.id == worker)).role == ADMIN


def test_last_admin_guard_applies_between_administrators(client, db, make_user) -> None:
    first = make_user(email="first@example.com", role=ADMIN)
    second = make_user(email="second@example.com", role=ADMIN)
    h1 = token_for(client, "first@example.com")
    assert client.patch(f"/api/users/{second.id}/role", headers=h1, json={"role": "EMPLOYEE"}).status_code == 200
    h2 = token_for(client, "second@example.com")
    assert client.patch(f"/api/users/{first.id}/role", headers=h2, json={"role": "EMPLOYEE"}).status_code == 403  # no longer ADMIN
    third = make_user(email="third@example.com", role=ADMIN)
    h3 = token_for(client, "third@example.com")
    # Two active admins (first, third): third may deactivate first, then first's tokens die and third is the last.
    assert client.patch(f"/api/users/{first.id}/status", headers=h3, json={"is_active": False}).status_code == 200
    assert me(client, h1) == 401
    response = client.patch(f"/api/users/{third.id}/role", headers=h3, json={"role": "HR"})
    assert response.status_code == 403  # self
    reactivated = client.patch(f"/api/users/{first.id}/status", headers=h3, json={"is_active": True})
    assert reactivated.status_code == 200
    db.expire_all()
    assert db.scalar(select(User).where(User.id == first.id)).is_active is True


def test_last_admin_guard_itself(db, make_user) -> None:
    """Through the API the acting administrator always remains an active ADMIN (no self-changes), so the guard is
    defense in depth: tested directly here so it keeps working if the self rule ever changes."""
    from fastapi import HTTPException

    from app.api.users import LAST_ADMIN, _keep_an_admin

    only = make_user(email="only.admin@example.com", role=ADMIN)
    with pytest.raises(HTTPException) as exc:
        _keep_an_admin(db, only)
    assert (exc.value.status_code, exc.value.detail) == (409, LAST_ADMIN)
    make_user(email="another.admin@example.com", role=ADMIN)
    _keep_an_admin(db, only)  # another active ADMIN exists: allowed
    _keep_an_admin(db, make_user(email="plain@example.com"))  # not an ADMIN: nothing to guard


def test_revoke_sessions_including_ones_own(client, accounts) -> None:
    worker = token_for(client, "worker@example.com")
    assert client.post(f"/api/users/{accounts['worker'].id}/revoke-sessions", headers=accounts["admin_h"]).status_code == 200
    assert me(client, worker) == 401 and me(client, token_for(client, "worker@example.com")) == 200
    assert client.post(f"/api/users/{accounts['admin'].id}/revoke-sessions", headers=accounts["admin_h"]).status_code == 200
    assert me(client, accounts["admin_h"]) == 401


def test_account_audit_rows_hold_no_credentials(client, db, accounts) -> None:
    uid = accounts["worker"].id
    client.patch(f"/api/users/{uid}/role", headers=accounts["admin_h"], json={"role": "MANAGER"})
    client.patch(f"/api/users/{uid}/status", headers=accounts["admin_h"], json={"is_active": False})
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action.like("user.%"))))
    assert {r.action for r in rows} == {"user.role_changed", "user.deactivated"}
    text = " ".join(str(r.details) for r in rows)
    assert all(word not in text.lower() for word in ("password", "hash", "token\":", "jti", TEST_PASSWORD.lower()))
    assert next(r for r in rows if r.action == "user.role_changed").details == {"from": "EMPLOYEE", "to": "MANAGER",
                                                                                 "sessions_ended": True}


def test_unknown_users_are_404_and_bodies_are_validated(client, accounts) -> None:
    h = accounts["admin_h"]
    assert client.get("/api/users/99999", headers=h).status_code == 404
    assert client.patch(f"/api/users/{accounts['worker'].id}/role", headers=h, json={"role": "ROOT"}).status_code == 422
    assert client.patch(f"/api/users/{accounts['worker'].id}/status", headers=h,
                        json={"is_active": False, "token_version": 0}).status_code == 422


# --- Ended employment (ADMIN/HR) ---------------------------------------------------------------------------------------


def test_admin_or_hr_with_an_inactive_employee_record_loses_access(client, db, staff, make_user) -> None:
    hr_emp, _ = staff(HR, email="hr.leaver@example.com", department="People")
    headers = token_for(client, "hr.leaver@example.com")
    assert me(client, headers) == 200
    hr_emp.status = EmploymentStatus.INACTIVE
    db.commit()
    assert me(client, headers) == 401
    assert client.get("/api/employees", headers=headers).status_code == 401
    assert login(client, "hr.leaver@example.com").status_code == 403
    failure = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")).all()[-1]
    assert failure.details == {"reason": "employment_inactive"}
    hr_emp.status = EmploymentStatus.ON_LEAVE  # still employed: access returns
    db.commit()
    assert me(client, token_for(client, "hr.leaver@example.com")) == 200


def test_departed_employee_keeps_self_service_only(client, db, staff) -> None:
    """Unchanged policy (Sub-prompt 26): EMPLOYEE/MANAGER logins of departed staff keep self-service access."""
    employee, _ = staff(EMPLOYEE, email="leaver@example.com", department="Engineering",
                        status=EmploymentStatus.INACTIVE)
    headers = token_for(client, "leaver@example.com")
    assert client.get(f"/api/employees/{employee.id}", headers=headers).status_code == 200


# --- Rate limiting --------------------------------------------------------------------------------------------------------


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("AUTH_LOGIN_MAX_ATTEMPTS_PER_CLIENT", "8")
    monkeypatch.setenv("AUTH_REGISTER_MAX_PER_CLIENT", "2")
    monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_repeated_failures_are_limited_the_same_for_existing_and_unknown_accounts(client, make_user, small_limits) -> None:
    make_user(email="target@example.com")
    for email in ("target@example.com", "nobody@example.com"):
        statuses = [login(client, email, "Wrong-Password-123!").status_code for _ in range(4)]
        assert statuses == [401, 401, 401, 429], email
        blocked = login(client, email, "Wrong-Password-123!")
        assert blocked.status_code == 429 and blocked.json() == {"detail": "Too many attempts. Try again later."}
        assert int(blocked.headers["retry-after"]) >= 1
    # Even the correct password is refused while the account key is limited (no oracle, no bypass).
    assert login(client, "target@example.com").status_code == 429


def test_success_resets_failures_and_window_expiry_resets_limits(client, make_user, small_limits, monkeypatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(rate_limit.limiter, "_clock", clock)
    make_user(email="target@example.com")
    assert [login(client, "target@example.com", "Wrong-Password-123!").status_code for _ in range(2)] == [401, 401]
    assert login(client, "target@example.com").status_code == 200  # success resets the failure count
    assert [login(client, "target@example.com", "Wrong-Password-123!").status_code for _ in range(3)] == [401, 401, 401]
    assert login(client, "target@example.com").status_code == 429
    clock.now += 61  # the window has passed
    assert login(client, "target@example.com").status_code == 200


def test_per_client_attempt_limit_and_registration_limit(client, make_user, small_limits) -> None:
    for i in range(8):
        assert login(client, f"user{i}@example.com", "Wrong-Password-123!").status_code == 401
    assert login(client, "fresh@example.com", "Wrong-Password-123!").status_code == 429
    body = {"full_name": "New Person", "password": TEST_PASSWORD}
    assert client.post("/api/auth/register", json={**body, "email": "a@example.com"}).status_code == 201
    assert client.post("/api/auth/register", json={**body, "email": "b@example.com"}).status_code == 201
    third = client.post("/api/auth/register", json={**body, "email": "c@example.com"})
    assert third.status_code == 429 and "retry-after" in third.headers


def test_rate_limited_requests_leave_no_credentials_in_logs_or_audit(client, db, make_user, small_limits, caplog) -> None:
    secret = "Wrong-Password-For-Logs-7!"
    with caplog.at_level("DEBUG"):
        for _ in range(5):
            login(client, "target@example.com", secret)
    text = caplog.text + " ".join(f"{r.details} {r.entity_id}" for r in db.scalars(select(AuditLog)))
    assert secret not in text and "target@example.com" not in text
    assert "rate limit reached on login" in caplog.text


def test_limiter_failure_fails_open_and_is_logged(client, make_user, monkeypatch, caplog) -> None:
    make_user(email="target@example.com")

    def broken(*args, **kwargs):
        raise RuntimeError("limiter state corrupted")

    monkeypatch.setattr(rate_limit.limiter, "retry_after", broken)
    monkeypatch.setattr(rate_limit.limiter, "record", broken)
    with caplog.at_level("WARNING"):
        assert login(client, "target@example.com").status_code == 200
    assert "rate limiter unavailable (RuntimeError)" in caplog.text
    assert "target@example.com" not in caplog.text


def test_rate_limits_can_be_disabled_only_outside_staging_and_production(client, make_user, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("AUTH_LOGIN_MAX_FAILURES", "1")
    get_settings.cache_clear()
    assert [login(client, "x@example.com", "Wrong-Password-123!").status_code for _ in range(3)] == [401, 401, 401]
    with pytest.raises(ValueError, match="AUTH_RATE_LIMIT_ENABLED"):
        Settings(_env_file=None, **{**_deployed(), "auth_rate_limit_enabled": False})


def test_limiter_memory_is_bounded() -> None:
    limiter = rate_limit.SlidingWindowLimiter(max_keys=3, clock=FakeClock())
    for key in "abcd":
        limiter.record(key, 60)
    assert list(limiter._events) == ["b", "c", "d"]


# --- Deployment configuration ------------------------------------------------------------------------------------------


def _deployed(**overrides) -> dict:
    values = {"db_user": "app", "db_password": "placeholder-password", "jwt_secret_key": TEST_ENV["JWT_SECRET_KEY"],
              "environment": "production", "cors_origins": ["https://hr.example.com"], "allowed_hosts": ["api.example.com"]}
    return {**values, **overrides}


@pytest.mark.parametrize(("overrides", "field"), [
    ({"cors_origins": ["*"]}, "CORS_ORIGINS"),
    ({"cors_origins": ["http://hr.example.com"]}, "https"),
    ({"allowed_hosts": ["*"]}, "ALLOWED_HOSTS"),
    ({"environment": "staging", "allowed_hosts": ["*"]}, "ALLOWED_HOSTS"),
    ({"environment": "staging", "cors_origins": ["*"]}, "CORS_ORIGINS"),
])
def test_staging_and_production_refuse_insecure_settings(overrides, field) -> None:
    with pytest.raises(ValueError, match=field):
        Settings(_env_file=None, **_deployed(**overrides))


def test_configuration_errors_never_echo_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", '["*"]')
    get_settings.cache_clear()
    with pytest.raises(ConfigurationError) as exc:
        get_settings()
    message = str(exc.value)
    assert "CORS_ORIGINS" in message
    assert TEST_ENV["DB_PASSWORD"] not in message and TEST_ENV["JWT_SECRET_KEY"] not in message


def test_production_hides_api_docs_and_checks_host_headers() -> None:
    settings = Settings(_env_file=None, **_deployed())
    assert settings.docs_enabled is False and settings.is_deployed
    client = TestClient(create_app(settings), base_url="http://api.example.com")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    assert client.get("/api/health").status_code == 200
    assert TestClient(create_app(settings), base_url="http://evil.example.net").get("/api/health").status_code == 400
    assert Settings(_env_file=None, **_deployed(api_docs_enabled=True)).docs_enabled is True
    assert Settings(_env_file=None, **_deployed(environment="development", allowed_hosts=["*"])).docs_enabled is True


def test_security_headers_on_every_response(client) -> None:
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
