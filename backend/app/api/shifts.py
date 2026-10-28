"""Shift definitions. No DELETE: deactivate instead, so historical attendance keeps its shift."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.models import Shift, User
from app.schemas.common import Page
from app.schemas.shift import (
    ShiftCreate,
    ShiftListParams,
    ShiftRead,
    ShiftStatusUpdate,
    ShiftUpdate,
    validate_shift_window,
)
from app.services.audit import apply_changes, record_audit
from app.services.pagination import paginate

router = APIRouter(prefix="/api/shifts", tags=["shifts"])

Writer = Annotated[User, Depends(require_roles(*permissions.SHIFT_WRITE))]
Reader = Annotated[User, Depends(require_roles(*permissions.SHIFT_READ))]

DUPLICATE_NAME = "A shift with this name already exists"
NOT_FOUND = "Shift not found"


def _get_or_404(db: DbSession, shift_id: int) -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return shift


def _name_taken(db: DbSession, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(Shift.id).where(func.lower(Shift.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Shift.id != exclude_id)
    return db.scalar(stmt) is not None


def _commit(db: DbSession) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME) from None


@router.post("", response_model=ShiftRead, status_code=status.HTTP_201_CREATED)
def create_shift(payload: ShiftCreate, db: DbSession, actor: Writer) -> Shift:
    if _name_taken(db, payload.name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME)

    shift = Shift(**payload.model_dump())
    db.add(shift)
    db.flush()
    record_audit(
        db, actor=actor, action="shift.created", entity_type="shift", entity_id=shift.id,
        details={"name": shift.name},
    )
    _commit(db)
    return shift


@router.get("", response_model=Page[ShiftRead])
def list_shifts(
    params: Annotated[ShiftListParams, Query()], db: DbSession, user: Reader
) -> Page[ShiftRead]:
    stmt = select(Shift).order_by(Shift.start_time.asc(), Shift.name.asc(), Shift.id.asc())
    if user.role not in permissions.SHIFT_READ_INACTIVE:
        stmt = stmt.where(Shift.is_active.is_(True))
    elif params.is_active is not None:
        stmt = stmt.where(Shift.is_active.is_(params.is_active))

    items, total = paginate(db, stmt, params)
    return Page[ShiftRead].build([ShiftRead.model_validate(s) for s in items], total, params)


@router.get("/{shift_id}", response_model=ShiftRead)
def get_shift(shift_id: int, db: DbSession, user: Reader) -> Shift:
    shift = _get_or_404(db, shift_id)
    if not shift.is_active and user.role not in permissions.SHIFT_READ_INACTIVE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return shift


@router.patch("/{shift_id}", response_model=ShiftRead)
def update_shift(shift_id: int, payload: ShiftUpdate, db: DbSession, actor: Writer) -> Shift:
    shift = _get_or_404(db, shift_id)
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)

    if "name" in changes and _name_taken(db, changes["name"], exclude_id=shift.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME)
    try:
        validate_shift_window(
            changes.get("start_time", shift.start_time),
            changes.get("end_time", shift.end_time),
            changes.get("break_minutes", shift.break_minutes),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None

    diff = apply_changes(shift, changes)
    if diff:
        record_audit(
            db, actor=actor, action="shift.updated", entity_type="shift", entity_id=shift.id,
            details={"changes": diff},
        )
        _commit(db)
    return shift


@router.patch("/{shift_id}/status", response_model=ShiftRead)
def change_shift_status(
    shift_id: int, payload: ShiftStatusUpdate, db: DbSession, actor: Writer
) -> Shift:
    shift = _get_or_404(db, shift_id)
    diff = apply_changes(shift, {"is_active": payload.is_active})
    if diff:
        details: dict[str, Any] = {"changes": diff}
        if payload.reason:
            details["reason"] = payload.reason
        record_audit(
            db, actor=actor, action="shift.status_changed", entity_type="shift", entity_id=shift.id,
            details=details,
        )
        _commit(db)
    return shift
