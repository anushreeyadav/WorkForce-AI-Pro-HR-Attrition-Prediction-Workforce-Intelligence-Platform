"""Minimal in-app notifications: each user can list and mark only their own."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.models import Notification, User
from app.models.base import utcnow
from app.schemas.common import Page
from app.schemas.notification import NotificationListParams, NotificationRead
from app.services.notifications import priority_rank_expression
from app.services.pagination import paginate

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

Reader = Annotated[User, Depends(require_roles(*permissions.NOTIFICATIONS_READ))]


@router.get("/me", response_model=Page[NotificationRead])
def my_notifications(
    params: Annotated[NotificationListParams, Query()], db: DbSession, user: Reader
) -> Page[NotificationRead]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if params.unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    if params.sort == "priority":
        stmt = stmt.order_by(Notification.is_read.asc(), priority_rank_expression().desc(),
                             Notification.created_at.desc(), Notification.id.desc())
    else:
        stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
    items, total = paginate(db, stmt, params)
    return Page[NotificationRead].build([NotificationRead.model_validate(n) for n in items], total, params)


@router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_notification_read(notification_id: int, db: DbSession, user: Reader) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = utcnow()
        db.commit()
    return notification
