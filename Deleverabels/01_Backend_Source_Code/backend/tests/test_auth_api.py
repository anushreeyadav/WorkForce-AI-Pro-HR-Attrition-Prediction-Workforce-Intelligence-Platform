import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, verify_password
from app.models import User, UserRole
from tests.conftest import TEST_ENV, TEST_PASSWORD, auth_header

SAFE_USER_FIELDS = {"id", "full_name", "email", "role", "is_active"}


def _login(client, email: str = "alice@example.com", password: str = TEST_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- Registration -----------------------------------------------------------------------------------


def test_register_creates_employee_with_hash_only(client, db) -> None:
    response = client.post(
        "/api/auth/register",
        json={"full_name": "  Bob Builder ", "email": "Bob@Example.COM", "password": TEST_PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == SAFE_USER_FIELDS
    assert body["email"] == "bob@example.com"
    assert body["full_name"] == "Bob Builder"
    assert body["role"] == "EMPLOYEE"
    assert body["is_active"] is True
    assert TEST_PASSWORD not in response.text
    assert "$2b$" not in response.text

    user = db.scalar(select(User).where(User.email == "bob@example.com"))
    assert user.password_hash != TEST_PASSWORD
    assert verify_password(TEST_PASSWORD, user.password_hash)


def test_duplicate_registration_is_409_case_insensitive(client) -> None:
    data = {"full_name": "Bob", "email": "bob@example.com", "password": TEST_PASSWORD}
    assert client.post("/api/auth/register", json=data).status_code == 201

    response = client.post("/api/auth/register", json={**data, "email": "BOB@example.com"})
    assert response.status_code == 409
    assert response.json() == {"detail": "Email is already registered"}


@pytest.mark.parametrize("role", ["ADMIN", "HR", "MANAGER", "EMPLOYEE", "admin"])
def test_public_registration_cannot_assign_role(client, db, role: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={"full_name": "Mallory", "email": "mallory@example.com", "password": TEST_PASSWORD, "role": role},
    )
    assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"full_name": "Bob", "email": "bob@example.com", "password": "too-short"},
        {"full_name": "Bob", "email": "bob@example.com", "password": "a" * 73},
        {"full_name": "Bob", "email": "bob@example.com", "password": "é" * 40},
        {"full_name": "", "email": "bob@example.com", "password": TEST_PASSWORD},
        {"full_name": "Bob", "email": "bob", "password": TEST_PASSWORD},
        {"full_name": "Bob", "email": "bob@example.com", "password": TEST_PASSWORD, "is_active": False},
    ],
    ids=["short", "too-many-bytes-ascii", "too-many-bytes-utf8", "blank-name", "bad-email", "extra-field"],
)
def test_register_validation_errors(client, payload: dict) -> None:
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422
    # Validation errors must not echo the submitted password back.
    assert payload["password"] not in response.text


# --- Login ------------------------------------------------------------------------------------------


def test_login_success_returns_token_and_safe_user(client, make_user, db) -> None:
    user = make_user(email="alice@example.com")
    assert user.last_login_at is None

    response = _login(client, email="ALICE@Example.com")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 1800
    assert set(body["user"]) == SAFE_USER_FIELDS
    assert body["user"]["id"] == user.id
    assert "password" not in response.text
    assert "$2b$" not in response.text

    db.expire_all()
    refreshed = db.get(User, user.id)
    assert refreshed.last_login_at is not None
    assert abs(datetime.now(UTC) - refreshed.last_login_at) < timedelta(minutes=1)


@pytest.mark.parametrize(
    "email, password",
    [("alice@example.com", "Wrong-Password-123"), ("nobody@example.com", TEST_PASSWORD)],
    ids=["wrong-password", "unknown-email"],
)
def test_invalid_credentials_generic_401(client, make_user, email: str, password: str) -> None:
    make_user()
    response = _login(client, email=email, password=password)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_oversized_login_password_is_401_not_500(client, make_user) -> None:
    make_user()
    assert _login(client, password="a" * 200).status_code == 401


def test_inactive_user_cannot_login(client, make_user) -> None:
    make_user(is_active=False)
    response = _login(client)
    assert response.status_code == 403
    assert response.json() == {"detail": "Account is inactive"}
    assert "access_token" not in response.text


def test_wrong_password_for_inactive_user_is_generic_401(client, make_user) -> None:
    make_user(is_active=False)
    assert _login(client, password="Wrong-Password-123").status_code == 401


def test_no_secrets_in_logs(client, make_user, caplog) -> None:
    make_user()
    with caplog.at_level(logging.DEBUG):
        token = _login(client).json()["access_token"]
        client.get("/api/auth/me", headers=auth_header(token))
        _login(client, password="Wrong-Password-123")
    for secret in (TEST_PASSWORD, "Wrong-Password-123", token, TEST_ENV["JWT_SECRET_KEY"], "$2b$"):
        assert secret not in caplog.text


# --- /me and bearer handling ---------------------------------------------------------------------------


def test_me_returns_safe_fields_only(client, make_user) -> None:
    user = make_user(role=UserRole.MANAGER)
    token = _login(client).json()["access_token"]

    response = client.get("/api/auth/me", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json() == {
        "id": user.id,
        "full_name": "Alice Example",
        "email": "alice@example.com",
        "role": "MANAGER",
        "is_active": True,
    }


@pytest.mark.parametrize(
    "headers, detail",
    [
        ({}, "Not authenticated"),
        ({"Authorization": "Bearer"}, "Not authenticated"),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, "Not authenticated"),
        ({"Authorization": "Bearer not.a.jwt"}, "Invalid or expired token"),
        ({"Authorization": "Bearer abc"}, "Invalid or expired token"),
    ],
    ids=["missing", "empty-bearer", "basic-scheme", "malformed", "garbage"],
)
def test_me_rejects_missing_or_invalid_token(client, headers: dict, detail: str) -> None:
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": detail}
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_expired_token(client, make_user) -> None:
    user = make_user()
    token = create_access_token(user.id, now=datetime.now(UTC) - timedelta(hours=1)).token
    response = client.get("/api/auth/me", headers=auth_header(token))
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token"}


def test_token_for_deleted_user_rejected(client, make_user, db) -> None:
    user = make_user()
    token = create_access_token(user.id).token
    db.delete(user)
    db.commit()
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 401


def test_token_stops_working_when_user_deactivated(client, make_user, db) -> None:
    user = make_user()
    token = _login(client).json()["access_token"]
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 200

    user.is_active = False
    db.commit()
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 401


# --- Logout -----------------------------------------------------------------------------------------


def test_logout_requires_auth_and_revokes_the_token(client, make_user) -> None:
    make_user()
    assert client.post("/api/auth/logout").status_code == 401

    token = _login(client).json()["access_token"]
    other_session = _login(client).json()["access_token"]
    response = client.post("/api/auth/logout", headers=auth_header(token))
    assert response.status_code == 200
    assert "revoked" in response.json()["detail"]

    # Since Sub-prompt 31 the token is revoked on the server (revoked_tokens), not just discarded by the client.
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 401
    assert client.post("/api/auth/logout", headers=auth_header(token)).status_code == 401
    # Only that token: the account's other session keeps working.
    assert client.get("/api/auth/me", headers=auth_header(other_session)).status_code == 200


def test_openapi_documents_bearer_security(client) -> None:
    schema = client.app.openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "Revokes the presented access token on the server" in schema["paths"]["/api/auth/logout"]["post"]["description"]


# --- Database outage ----------------------------------------------------------------------------------


class _UnavailableSession:
    def scalar(self, *args, **kwargs):
        from sqlalchemy.exc import OperationalError

        raise OperationalError(
            "SELECT", {}, Exception("(1045) Access denied for user 'pytest_user'@'db-host'")
        )


@pytest.mark.parametrize(
    "path, payload",
    [
        ("/api/auth/register", {"full_name": "Bob", "email": "bob@example.com", "password": TEST_PASSWORD}),
        ("/api/auth/login", {"email": "bob@example.com", "password": TEST_PASSWORD}),
    ],
)
def test_database_outage_returns_503_without_details(path: str, payload: dict, caplog) -> None:
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: _UnavailableSession()
    with caplog.at_level(logging.DEBUG):
        response = TestClient(app).post(path, json=payload)

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    for leaked in ("pytest_user", "db-host", "Access denied", TEST_PASSWORD):
        assert leaked not in response.text
        assert leaked not in caplog.text
