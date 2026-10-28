import warnings
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from jwt.warnings import InsecureKeyLengthWarning
from pydantic import ValidationError

from app.core import security
from app.core.security import (
    InvalidTokenError,
    PasswordTooLongError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.schemas.auth import LoginRequest, RegisterRequest
from tests.conftest import TEST_ENV

SECRET = TEST_ENV["JWT_SECRET_KEY"]
ISSUER = TEST_ENV["JWT_ISSUER"]


# --- Password hashing -----------------------------------------------------------------------------


def test_hash_and_verify_roundtrip() -> None:
    hashed = hash_password("Correct-Horse-Battery-9")
    assert hashed.startswith("$2b$")
    assert "Correct-Horse-Battery-9" not in hashed
    assert verify_password("Correct-Horse-Battery-9", hashed)
    assert not verify_password("correct-horse-battery-9", hashed)
    assert not verify_password("", hashed)


def test_hashes_are_salted() -> None:
    assert hash_password("same-password-123") != hash_password("same-password-123")


def test_production_cost_factor_is_12(monkeypatch) -> None:
    # The autouse fixture lowers BCRYPT_ROUNDS for speed; undo it to see the real default.
    monkeypatch.undo()
    assert security.BCRYPT_ROUNDS == 12
    assert hash_password("Correct-Horse-Battery-9").startswith("$2b$12$")


def test_non_ascii_password_roundtrip() -> None:
    password = "pässwörd-日本語-🔐"
    assert verify_password(password, hash_password(password))
    assert not verify_password("passwort-日本語-🔐", hash_password(password))


def test_exactly_72_bytes_allowed_73_rejected_never_truncated() -> None:
    at_limit = "a" * 72
    hashed = hash_password(at_limit)
    assert verify_password(at_limit, hashed)
    # bcrypt would ignore byte 73+; we must reject rather than accept a longer password as equal.
    assert not verify_password(at_limit + "b", hashed)
    with pytest.raises(PasswordTooLongError):
        hash_password("a" * 73)


def test_multibyte_byte_limit() -> None:
    # 18 four-byte emoji = 72 bytes (ok); 19 = 76 bytes (rejected) despite only 19 characters.
    assert verify_password("🔐" * 18, hash_password("🔐" * 18))
    with pytest.raises(PasswordTooLongError):
        hash_password("🔐" * 19)


def test_verify_with_malformed_hash_returns_false() -> None:
    assert not verify_password("whatever-password", "not-a-bcrypt-hash")


# --- Request schema validation -----------------------------------------------------------------------


def _register(**overrides):
    data = {"full_name": "Alice", "email": "alice@example.com", "password": "Correct-Horse-Battery-9"}
    data.update(overrides)
    return RegisterRequest(**data)


def test_register_password_min_length() -> None:
    with pytest.raises(ValidationError, match="at least 12 characters"):
        _register(password="Short-pw-1")
    assert _register(password="x" * 11 + "y").password == "x" * 11 + "y"


def test_register_password_byte_limit() -> None:
    assert _register(password="a" * 72)
    with pytest.raises(ValidationError):
        _register(password="a" * 73)
    with pytest.raises(ValidationError, match="at most 72 bytes"):
        _register(password="é" * 40)  # 40 characters, 80 bytes


@pytest.mark.parametrize("password", ["\x00" + "a" * 20, " " * 20])
def test_register_rejects_null_or_blank_passwords(password: str) -> None:
    with pytest.raises(ValidationError):
        _register(password=password)


def test_register_normalizes_email_and_strips_name() -> None:
    request = _register(email="  Alice.Smith@Example.COM ", full_name="  Alice Smith  ")
    assert request.email == "alice.smith@example.com"
    assert request.full_name == "Alice Smith"


def test_register_rejects_invalid_email_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _register(email="not-an-email")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _register(role="ADMIN")


def test_login_request_normalizes_email() -> None:
    assert LoginRequest(email="BOB@Example.com", password="x").email == "bob@example.com"


# --- JWT --------------------------------------------------------------------------------------------


def _claims(**overrides) -> dict:
    now = datetime.now(UTC)
    claims = {
        "sub": "1",
        "iss": ISSUER,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": "abc123",
        "type": "access",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def test_token_roundtrip_and_claims() -> None:
    access = create_access_token(42)
    assert access.expires_in == 30 * 60

    payload = decode_access_token(access.token)
    assert payload.user_id == 42
    assert payload.jti

    claims = jwt.decode(access.token, SECRET, algorithms=["HS256"], issuer=ISSUER)
    # "ver" (Sub-prompt 31) is the account's session version; still no role, email or other personal data.
    assert set(claims) == {"sub", "iss", "iat", "nbf", "exp", "jti", "type", "ver"}
    assert claims["ver"] == 0 and payload.version == 0
    assert claims["exp"] - claims["iat"] == 30 * 60
    assert jwt.get_unverified_header(access.token)["alg"] == "HS256"


def test_each_token_has_unique_jti() -> None:
    assert decode_access_token(create_access_token(1).token).jti != decode_access_token(
        create_access_token(1).token
    ).jti


def test_expired_token_rejected() -> None:
    token = create_access_token(1, now=datetime.now(UTC) - timedelta(hours=1)).token
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def _hs512_token() -> str:
    # Deliberately signs with an algorithm the app is not configured for; PyJWT warns about the
    # key length for HS512, which is expected here and silenced only for this call.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureKeyLengthWarning)
        return jwt.encode(_claims(), SECRET, algorithm="HS512")


@pytest.mark.parametrize(
    "token",
    [
        jwt.encode(_claims(), "a-completely-different-secret-value-0123456789", algorithm="HS256"),
        jwt.encode(_claims(iss="someone-else"), SECRET, algorithm="HS256"),
        jwt.encode(_claims(type="refresh"), SECRET, algorithm="HS256"),
        jwt.encode(_claims(sub="not-a-number"), SECRET, algorithm="HS256"),
        jwt.encode(_claims(sub="0"), SECRET, algorithm="HS256"),
        jwt.encode(_claims(nbf=datetime.now(UTC) + timedelta(hours=1)), SECRET, algorithm="HS256"),
        _hs512_token(),  # algorithm not configured
        jwt.encode(_claims(), None, algorithm="none"),
        "not.a.jwt",
        "",
        "Bearer abc",
    ],
    ids=["bad-signature", "wrong-issuer", "wrong-type", "non-numeric-sub", "zero-sub",
         "not-yet-valid", "unexpected-alg", "alg-none", "garbage", "empty", "prefixed"],
)
def test_invalid_tokens_rejected(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


@pytest.mark.parametrize("missing", ["sub", "iss", "iat", "exp", "jti", "type"])
def test_missing_required_claim_rejected(missing: str) -> None:
    token = jwt.encode(_claims(**{missing: None}), SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)
