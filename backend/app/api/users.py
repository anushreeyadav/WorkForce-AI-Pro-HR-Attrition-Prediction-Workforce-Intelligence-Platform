"""Login-account administration (ADMIN only): list accounts, deactivate or reactivate, change role, end sessions.

- Every change is one transaction with its audit row. Audit details hold statuses and roles only: never passwords,
  password hashes, tokens or token ids.
- Deactivation, role changes and "revoke sessions" increment users.token_version, so every access token issued before
  stops working on its next request (see app/api/deps.get_current_user). Reactivation does not revive old tokens.
- Nobody changes their own status or role here (no self-elevation, no locking oneself out), and the last active ADMIN
  can be neither deactivated nor demoted. Ending one's own sessions is allowed (for example after a token leak).
- Passwords are not managed here (no reset flow yet), and accounts are never deleted (audit history keeps them).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.models import Employee, User, UserRole
from app.schemas.common import Page
from app.schemas.user import UserAdminRead, UserListParams, UserRoleUpdate, UserStatusUpdate
from app.services.audit import record_audit
from app.services.pagination import paginate
from app.services.search import LIKE_ESCAPE, contains_pattern

router = APIRouter(prefix="/api/users", tags=["users"])

Admin = Annotated[User, Depends(require_roles(*permissions.USER_ADMIN))]
NOT_FOUND = "User not found"
LAST_ADMIN = "At least one active administrator must remain"


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _read(db: DbSession, user: User) -> UserAdminRead:
    employee = db.execute(select(Employee.id, Employee.status).where(Employee.user_id == user.id)).first()
    return UserAdminRead.model_validate(user).model_copy(update={
        "employee_id": employee[0] if employee else None, "employee_status": employee[1] if employee else None})


def _load(db: DbSession, user_id: int) -> User:
    user = db.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    if user is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return user


def _not_self(actor: User, target: User) -> None:
    if actor.id == target.id:
        raise _error(status.HTTP_403_FORBIDDEN, "You cannot change your own account")


def _keep_an_admin(db: DbSession, target: User) -> None:
    """Refuse to remove the last active ADMIN. The rows are locked (MySQL), so two administrators cannot remove each
    other concurrently."""
    if not (target.role == UserRole.ADMIN and target.is_active):
        return
    others = db.scalars(select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True), User.id != target.id)
                        .with_for_update()).all()
    if not others:
        raise _error(status.HTTP_409_CONFLICT, LAST_ADMIN)


def _end_sessions(db: DbSession, target: User) -> None:
    db.execute(update(User).where(User.id == target.id).values(token_version=User.token_version + 1)
               .execution_options(synchronize_session=False))


@router.get("", response_model=Page[UserAdminRead])
def list_users(params: Annotated[UserListParams, Query()], db: DbSession, _: Admin) -> Page[UserAdminRead]:
    stmt = select(User)
    if params.q:
        pattern = contains_pattern(params.q)
        stmt = stmt.where(or_(func.lower(User.full_name).like(pattern.lower(), escape=LIKE_ESCAPE),
                              func.lower(User.email).like(pattern.lower(), escape=LIKE_ESCAPE)))
    if params.role is not None:
        stmt = stmt.where(User.role == params.role)
    if params.is_active is not None:
        stmt = stmt.where(User.is_active.is_(params.is_active))
    items, total = paginate(db, stmt.order_by(User.email, User.id), params)
    employees = dict(db.execute(select(Employee.user_id, Employee)
                                .where(Employee.user_id.in_([u.id for u in items]))).all()) if items else {}
    reads = [UserAdminRead.model_validate(u).model_copy(update={
        "employee_id": employees[u.id].id if u.id in employees else None,
        "employee_status": employees[u.id].status if u.id in employees else None}) for u in items]
    return Page[UserAdminRead].build(reads, total, params)


@router.get("/{user_id}", response_model=UserAdminRead)
def get_user(user_id: int, db: DbSession, _: Admin) -> UserAdminRead:
    return _read(db, _load(db, user_id))


@router.patch("/{user_id}/status", response_model=UserAdminRead)
def change_user_status(user_id: int, payload: UserStatusUpdate, db: DbSession, actor: Admin) -> UserAdminRead:
    """Deactivate (every session ends immediately; sign-in is refused) or reactivate a login account."""
    target = _load(db, user_id)
    _not_self(actor, target)
    if target.is_active == payload.is_active:
        return _read(db, target)  # repeat request: nothing written
    if not payload.is_active:
        _keep_an_admin(db, target)
        db.execute(update(User).where(User.id == target.id).values(is_active=False).execution_options(synchronize_session=False))
        _end_sessions(db, target)
        record_audit(db, actor=actor, action="user.deactivated", entity_type="user", entity_id=target.id,
                     details={"role": target.role.value, "sessions_ended": True})
    else:
        db.execute(update(User).where(User.id == target.id).values(is_active=True).execution_options(synchronize_session=False))
        record_audit(db, actor=actor, action="user.reactivated", entity_type="user", entity_id=target.id,
                     details={"role": target.role.value})
    db.commit()
    return _read(db, _load(db, target.id))


@router.patch("/{user_id}/role", response_model=UserAdminRead)
def change_user_role(user_id: int, payload: UserRoleUpdate, db: DbSession, actor: Admin) -> UserAdminRead:
    """Change the role of another account. All its sessions end, so the new role applies from the next sign-in."""
    target = _load(db, user_id)
    _not_self(actor, target)
    if target.role == payload.role:
        return _read(db, target)
    if payload.role != UserRole.ADMIN:
        _keep_an_admin(db, target)
    previous = target.role
    db.execute(update(User).where(User.id == target.id).values(role=payload.role).execution_options(synchronize_session=False))
    _end_sessions(db, target)
    record_audit(db, actor=actor, action="user.role_changed", entity_type="user", entity_id=target.id,
                 details={"from": previous.value, "to": payload.role.value, "sessions_ended": True})
    db.commit()
    return _read(db, _load(db, target.id))


@router.post("/{user_id}/revoke-sessions", response_model=UserAdminRead)
def revoke_user_sessions(user_id: int, db: DbSession, actor: Admin) -> UserAdminRead:
    """End every session of the account (all access tokens issued so far). The account stays active."""
    target = _load(db, user_id)
    _end_sessions(db, target)
    record_audit(db, actor=actor, action="user.sessions_revoked", entity_type="user", entity_id=target.id,
                 details={"self": actor.id == target.id})
    db.commit()
    return _read(db, _load(db, target.id))
