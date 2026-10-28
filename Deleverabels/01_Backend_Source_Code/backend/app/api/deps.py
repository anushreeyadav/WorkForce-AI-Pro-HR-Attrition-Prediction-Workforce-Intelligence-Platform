"""Reusable request dependencies: database session, current user, and role checks."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import InvalidTokenError, TokenPayload, decode_access_token
from app.models import Employee, EmploymentStatus, RevokedToken, User, UserRole

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Access token from POST /api/auth/login, sent as `Authorization: Bearer <token>`.",
)

DbSession = Annotated[Session, Depends(get_db)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> TokenPayload:
    """Signature, issuer, expiry and claim checks (no database). Cached per request by FastAPI."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise _unauthorized("Not authenticated")
    try:
        return decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise _unauthorized("Invalid or expired token") from None


def get_current_user(db: DbSession, payload: Annotated[TokenPayload, Depends(get_token_payload)]) -> User:
    """The account behind a valid token, re-checked on every request in ONE query:
    - the account exists and is active;
    - the token's session version matches users.token_version (deactivation, role change and "revoke sessions"
      end all earlier tokens);
    - the token was not revoked (logout);
    - for ADMIN and HR, a linked employee record is not INACTIVE. Their access is organization-wide and cannot be
      narrowed, so it ends with their employment. MANAGER and EMPLOYEE accounts of departed staff are already reduced
      to self-service by the scope helpers (employee_access.managed_department_id). Ending any login completely is
      PATCH /api/users/{id}/status.
    Every failure gives the same 401. If the database cannot answer, the query raises and the request fails with 503:
    revocation fails closed, never open.
    """
    revoked = exists(select(RevokedToken.jti).where(RevokedToken.jti == payload.jti))
    row = db.execute(
        select(User, Employee.status, revoked)
        .outerjoin(Employee, Employee.user_id == User.id)
        .where(User.id == payload.user_id)
    ).first()
    if row is None:
        raise _unauthorized("Invalid or expired token")
    user, employment_status, is_revoked = row
    if not user.is_active or is_revoked or payload.version != user.token_version or employment_ended(user, employment_status):
        raise _unauthorized("Invalid or expired token")
    return user


ORG_WIDE_ROLES = frozenset({UserRole.ADMIN, UserRole.HR})


def employment_ended(user: User, employment_status: EmploymentStatus | None) -> bool:
    """An ADMIN or HR login whose linked employee record is INACTIVE must not be used (see get_current_user)."""
    return user.role in ORG_WIDE_ROLES and employment_status == EmploymentStatus.INACTIVE


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentToken = Annotated[TokenPayload, Depends(get_token_payload)]


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    """Dependency factory: `Depends(require_roles(UserRole.ADMIN, UserRole.HR))`.

    Roles are checked against the database record loaded by get_current_user, never against
    client-supplied data or token claims.
    """
    if not roles or not all(isinstance(role, UserRole) for role in roles):
        raise ValueError("require_roles() needs at least one UserRole")
    allowed = frozenset(roles)

    def _check_role(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return user

    # Read by the route-permission inventory test (tests/test_authorization_matrix.py); never used for decisions.
    _check_role.allowed_roles = allowed  # type: ignore[attr-defined]
    return _check_role
