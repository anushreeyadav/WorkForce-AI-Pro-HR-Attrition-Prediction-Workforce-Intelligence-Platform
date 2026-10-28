import logging

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentToken, CurrentUser, DbSession, employment_ended
from app.core.config import get_settings
from app.core.rate_limit import account_key, limiter
from app.core.security import burn_password_check, create_access_token, hash_password, verify_password
from app.models import Employee, RevokedToken, User, UserRole
from app.models.base import utcnow
from app.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LogoutResponse,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.user import UserRead
from app.services.audit import record_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_CREDENTIALS = "Invalid email or password"
RATE_LIMITED = "Too many attempts. Try again later."


# --- Rate limiting (see app/core/rate_limit.py for scope and limitations) ---------------------------------------------


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _retry_after(key: str, limit: int) -> int | None:
    """Fails OPEN: an internal limiter error never locks every user out of signing in. The error type (never a key,
    address or email) is logged so it can be noticed and fixed."""
    try:
        return limiter.retry_after(key, limit, get_settings().auth_rate_limit_window_seconds)
    except Exception as exc:  # noqa: BLE001 - availability over enforcement for this in-memory guard
        logger.warning("Authentication rate limiter unavailable (%s); request allowed", type(exc).__name__)
        return None


def _record(key: str) -> None:
    try:
        limiter.record(key, get_settings().auth_rate_limit_window_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Authentication rate limiter unavailable (%s); attempt not counted", type(exc).__name__)


def _too_many(retry_after: int, endpoint: str) -> HTTPException:
    logger.warning("Authentication rate limit reached on %s", endpoint)
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMITED,
                         headers={"Retry-After": str(retry_after)})


# --- Endpoints --------------------------------------------------------------------------------------------------------------


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: DbSession) -> User:
    """Create a standard EMPLOYEE account. Privileged roles can never be assigned here."""
    settings = get_settings()
    if settings.auth_rate_limit_enabled:
        key = f"register:{_client(request)}"
        if (wait := _retry_after(key, settings.auth_register_max_per_client)) is not None:
            raise _too_many(wait, "register")
        _record(key)
    if db.scalar(select(User.id).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")

    user = User(
        full_name=payload.full_name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=UserRole.EMPLOYEE,
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
        # Same transaction as the account: no audit row means no account. Only the id and role are recorded.
        record_audit(db, actor=user, action="auth.registered", entity_type="user", entity_id=user.id,
                     details={"role": user.role.value})
        db.commit()
    except IntegrityError:
        # Concurrent registration with the same email hit the unique constraint.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email is already registered"
        ) from None
    db.refresh(user)
    return user


def _login_failed(db: DbSession, user: User | None, reason: str) -> None:
    """Record a failed sign-in in its own transaction (the request itself fails). Never the submitted email or
    password: for an unknown account nothing identifies who tried."""
    record_audit(db, actor=user, action="auth.login_failed", entity_type="user",
                 entity_id=user.id if user is not None else None, details={"reason": reason})
    db.commit()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    """Rate limits (checked before any password work, identical for existing and unknown accounts):
    AUTH_LOGIN_MAX_ATTEMPTS_PER_CLIENT attempts per client address, and AUTH_LOGIN_MAX_FAILURES failed attempts per
    client address and submitted email, within AUTH_RATE_LIMIT_WINDOW_SECONDS. Over a limit: 429 with Retry-After."""
    settings = get_settings()
    client = _client(request)
    failures = account_key(client, payload.email)
    if settings.auth_rate_limit_enabled:
        per_client = f"login-client:{client}"
        wait = (_retry_after(per_client, settings.auth_login_max_attempts_per_client)
                or _retry_after(failures, settings.auth_login_max_failures))
        if wait is not None:
            raise _too_many(wait, "login")
        _record(per_client)

    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None:
        burn_password_check(payload.password)
        if settings.auth_rate_limit_enabled:
            _record(failures)
        _login_failed(db, None, "unknown_account")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)
    if not verify_password(payload.password, user.password_hash):
        if settings.auth_rate_limit_enabled:
            _record(failures)
        _login_failed(db, user, "invalid_password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)
    if not user.is_active:
        _login_failed(db, user, "inactive_account")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
    if employment_ended(user, db.scalar(select(Employee.status).where(Employee.user_id == user.id))):
        _login_failed(db, user, "employment_inactive")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")

    if settings.auth_rate_limit_enabled:
        limiter.reset(failures)
    user.last_login_at = utcnow()
    # Write the account row (exclusive row lock) BEFORE the audit row. The audit insert's foreign-key check takes a
    # shared lock on the same row; in the other order, two concurrent sign-ins of one account each held the shared
    # lock and waited for the exclusive one: an InnoDB deadlock (seen on MySQL 8.0 under load, Sub-prompt 32).
    db.flush()
    # Same transaction as last_login_at: if the audit row cannot be written, no token is issued.
    record_audit(db, actor=user, action="auth.login_succeeded", entity_type="user", entity_id=user.id)
    db.commit()
    db.refresh(user)

    access = create_access_token(user.id, token_version=user.token_version)
    return TokenResponse(
        access_token=access.token,
        expires_in=access.expires_in,
        user=UserRead.model_validate(user),
    )


@router.get("/me", response_model=UserRead)
def read_current_user(user: CurrentUser) -> User:
    return user


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Log out and revoke this access token",
    description=(
        "Revokes the presented access token on the server: every later request with it gets 401. Other sessions of "
        "the same account are not affected (an administrator can end all of them with "
        "`POST /api/users/{id}/revoke-sessions`). Repeating the request with the same token is a 401."
    ),
)
def logout(user: CurrentUser, token: CurrentToken, db: DbSession) -> LogoutResponse:
    now = utcnow()
    # Housekeeping: rows for tokens that have expired anyway are no longer needed (bounded by the expires_at index).
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
    db.add(RevokedToken(jti=token.jti, user_id=user.id, expires_at=token.expires_at, revoked_at=now))
    record_audit(db, actor=user, action="auth.logout", entity_type="user", entity_id=user.id)
    try:
        db.commit()
    except IntegrityError:
        # The same token was revoked by a concurrent request: the outcome is the same.
        db.rollback()
    return LogoutResponse(detail="Logged out. This access token has been revoked and can no longer be used.")


WRONG_CURRENT_PASSWORD = "Current password is incorrect"


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    summary="Change your own password and end every session",
    description=(
        "Requires the current password. On success the new password applies at once and every session of the account "
        "ends, including this one (the caller signs in again). A wrong current password is a 403 (never a 401, which "
        "would end the caller's session) and counts towards AUTH_LOGIN_MAX_FAILURES per account within "
        "AUTH_RATE_LIMIT_WINDOW_SECONDS; over the limit: 429 with Retry-After. Passwords are never logged or audited."
    ),
)
def change_password(payload: ChangePasswordRequest, user: CurrentUser, db: DbSession) -> ChangePasswordResponse:
    settings = get_settings()
    failures = f"password-change:{user.id}"
    if settings.auth_rate_limit_enabled and (
            wait := _retry_after(failures, settings.auth_login_max_failures)) is not None:
        raise _too_many(wait, "change-password")
    if not verify_password(payload.current_password, user.password_hash):
        if settings.auth_rate_limit_enabled:
            _record(failures)
        record_audit(db, actor=user, action="auth.password_change_failed", entity_type="user", entity_id=user.id,
                     details={"reason": "invalid_current_password"})
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=WRONG_CURRENT_PASSWORD)
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="The new password must be different from the current password")

    # Conditional on the hash just verified, so two concurrent changes cannot both succeed.
    changed = db.execute(
        update(User).where(User.id == user.id, User.password_hash == user.password_hash)
        .values(password_hash=hash_password(payload.new_password), token_version=User.token_version + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The password was changed concurrently; sign in again")
    record_audit(db, actor=user, action="auth.password_changed", entity_type="user", entity_id=user.id)
    db.commit()
    if settings.auth_rate_limit_enabled:
        limiter.reset(failures)
    return ChangePasswordResponse(detail="Password changed. Every session has ended; sign in with the new password.")
