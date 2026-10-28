"""Password hashing (bcrypt) and access-token handling (PyJWT). Nothing here logs secrets or tokens."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import bcrypt
import jwt

from app.core.config import get_settings

BCRYPT_MAX_BYTES = 72
BCRYPT_ROUNDS = 12
TOKEN_TYPE_ACCESS = "access"
REQUIRED_CLAIMS = ("sub", "iss", "iat", "exp", "jti", "type")
JTI_MAX_LENGTH = 64  # revoked_tokens.jti


class PasswordTooLongError(ValueError):
    pass


class InvalidTokenError(Exception):
    """Raised for any token that is malformed, expired, wrongly signed, or missing claims."""


# --- Passwords ------------------------------------------------------------------------------------


def password_byte_length(password: str) -> int:
    return len(password.encode("utf-8"))


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        # Never truncate: a truncated hash would silently accept other passwords.
        raise PasswordTooLongError(f"Password exceeds {BCRYPT_MAX_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time bcrypt comparison. Returns False (never raises) for oversized input or bad hashes."""
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except ValueError:
        return False


@lru_cache
def _dummy_hash() -> str:
    return hash_password(uuid.uuid4().hex)


def burn_password_check(password: str) -> None:
    """Run a bcrypt check against a dummy hash so unknown emails take as long as wrong passwords."""
    verify_password(password, _dummy_hash())


# --- Access tokens --------------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_in: int  # seconds


@dataclass(frozen=True)
class TokenPayload:
    user_id: int
    jti: str
    expires_at: datetime
    version: int = 0  # the account's token_version when the token was issued (0 for tokens issued before it existed)


def create_access_token(user_id: int, *, token_version: int = 0, now: datetime | None = None) -> AccessToken:
    """Claims are identifiers only; role and profile data are always read fresh from the database. "ver" is the
    account's session version: incrementing users.token_version invalidates every token issued before."""
    settings = get_settings()
    issued_at = now or datetime.now(UTC)
    lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    claims = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + lifetime,
        "jti": uuid.uuid4().hex,
        "type": TOKEN_TYPE_ACCESS,
        "ver": token_version,
    }
    token = jwt.encode(
        claims, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return AccessToken(token=token, expires_in=int(lifetime.total_seconds()))


def decode_access_token(token: str) -> TokenPayload:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError:
        raise InvalidTokenError from None

    if claims.get("type") != TOKEN_TYPE_ACCESS:
        raise InvalidTokenError
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError):
        raise InvalidTokenError from None
    if user_id <= 0 or not isinstance(claims["jti"], str) or not claims["jti"] or len(claims["jti"]) > JTI_MAX_LENGTH:
        raise InvalidTokenError
    version = claims.get("ver", 0)  # absent in tokens issued before session versions existed
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise InvalidTokenError

    return TokenPayload(
        user_id=user_id,
        jti=claims["jti"],
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        version=version,
    )
