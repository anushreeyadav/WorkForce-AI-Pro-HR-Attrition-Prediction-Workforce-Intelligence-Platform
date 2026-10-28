"""Minimal in-app notifications. Rows are added to the caller's session (same transaction as the change).

There is no email or push delivery yet; nothing here performs external side effects.
"""

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.models import Employee, EmploymentStatus, Notification, User, UserRole


# Display priority derived from the category only (no column, no scores). Unknown/older categories are NORMAL.
NOTIFICATION_PRIORITY = {"risk_alert_overdue": "URGENT", "risk_alert_due_soon": "HIGH", "risk_alert": "HIGH"}
PRIORITY_RANK = {"URGENT": 3, "HIGH": 2, "NORMAL": 1}


def priority_of(category: str) -> str:
    return NOTIFICATION_PRIORITY.get(category, "NORMAL")


def priority_rank_expression():
    """SQL equivalent of PRIORITY_RANK[priority_of(category)], for ordering."""
    return case(*[(Notification.category == c, PRIORITY_RANK[p]) for c, p in NOTIFICATION_PRIORITY.items()],
                else_=PRIORITY_RANK["NORMAL"])


def notify(
    db: Session,
    *,
    user_id: int,
    category: str,
    title: str,
    message: str,
    entity_type: str | None = None,
    entity_id: int | str | None = None,
) -> None:
    db.add(
        Notification(
            user_id=user_id,
            category=category,
            title=title,
            message=message,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
        )
    )


def department_manager_user_ids(db: Session, department_id: int, exclude_user_id: int | None = None) -> list[int]:
    """Active MANAGER logins linked to a current (not INACTIVE) employee record in the department."""
    stmt = (
        select(User.id)
        .join(Employee, Employee.user_id == User.id)
        .where(
            Employee.department_id == department_id,
            User.role == UserRole.MANAGER,
            User.is_active.is_(True),
            Employee.status != EmploymentStatus.INACTIVE,
        )
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return list(db.scalars(stmt).all())
