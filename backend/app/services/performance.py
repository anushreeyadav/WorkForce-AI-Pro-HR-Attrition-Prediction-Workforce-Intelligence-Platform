"""Performance management rules: visibility, roles, eligibility, cycle transitions and versioned history.

Cycle transitions (ADMIN/HR only):
    open           DRAFT        -> OPEN
    start_reviews  OPEN         -> IN_PROGRESS
    complete       IN_PROGRESS  -> COMPLETED   (every review FINALIZED or CANCELLED)
    cancel         DRAFT, OPEN, IN_PROGRESS -> CANCELLED (reason required)
Reviews can be created and edited only while their cycle is OPEN or IN_PROGRESS.

Review workflow:
    ASSIGNED --(employee submits self-assessment, by the self-assessment deadline)--> SELF_SUBMITTED
    ASSIGNED or SELF_SUBMITTED --(reviewer submits, by the manager deadline; from ASSIGNED only after the
        self-assessment deadline)--> MANAGER_SUBMITTED
    MANAGER_SUBMITTED --(ADMIN/HR finalize)--> FINALIZED
    FINALIZED --(ADMIN/HR reopen with a reason; correction)--> SELF_SUBMITTED or ASSIGNED
    any open status --(ADMIN/HR cancel with a reason)--> CANCELLED
Nobody reviews, finalizes, reopens or cancels their own review.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, exists, false, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import (
    Employee,
    Goal,
    PerformanceEvent,
    PerformanceEventType,
    PerformanceReview,
    ReviewCycle,
    ReviewCycleStatus,
    User,
    UserRole,
)
from app.models.performance import OPEN_REVIEW_STATUSES
from app.services.employee_access import managed_department_id
from app.services.tasks import Viewer, viewer  # noqa: F401  (shared viewer context, re-exported)

C = ReviewCycleStatus


@dataclass(frozen=True)
class CycleTransition:
    event: PerformanceEventType
    allowed_from: tuple[ReviewCycleStatus, ...]
    target: ReviewCycleStatus


CYCLE_TRANSITIONS = {
    "open": CycleTransition(PerformanceEventType.CYCLE_OPENED, (C.DRAFT,), C.OPEN),
    "start_reviews": CycleTransition(PerformanceEventType.CYCLE_REVIEWS_STARTED, (C.OPEN,), C.IN_PROGRESS),
    "complete": CycleTransition(PerformanceEventType.CYCLE_COMPLETED, (C.IN_PROGRESS,), C.COMPLETED),
    "cancel": CycleTransition(PerformanceEventType.CYCLE_CANCELLED, (C.DRAFT, C.OPEN, C.IN_PROGRESS), C.CANCELLED),
}


class PerformanceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class ConcurrentChange(Exception):
    """The record changed after it was loaded; nothing was written."""


# --- Visibility -----------------------------------------------------------------------------------------


def visible_cycles(v: Viewer, stmt: Select) -> Select:
    """ADMIN/HR: all. MANAGER: every non-draft cycle (cycles are organization-wide). EMPLOYEE: cycles in which they
    have a review or a goal."""
    if v.org_wide:
        return stmt
    if v.user.role == UserRole.MANAGER:
        return stmt.where(ReviewCycle.status != C.DRAFT)
    if v.employee_id is None:
        return stmt.where(false())
    return stmt.where(ReviewCycle.status != C.DRAFT, or_(
        exists(select(PerformanceReview.id).where(PerformanceReview.cycle_id == ReviewCycle.id,
                                                  PerformanceReview.employee_id == v.employee_id)),
        exists(select(Goal.id).where(Goal.cycle_id == ReviewCycle.id, Goal.employee_id == v.employee_id))))


def visible_goals(v: Viewer, stmt: Select) -> Select:
    stmt = stmt.join(Employee, Employee.id == Goal.employee_id)
    if v.org_wide:
        return stmt
    conditions = [Goal.employee_id == v.employee_id] if v.employee_id is not None else []
    if v.user.role == UserRole.MANAGER and v.department_id is not None:
        conditions.append(Employee.department_id == v.department_id)
    return stmt.where(or_(*conditions)) if conditions else stmt.where(false())


def visible_reviews(v: Viewer, stmt: Select) -> Select:
    stmt = stmt.join(Employee, Employee.id == PerformanceReview.employee_id)
    if v.org_wide:
        return stmt
    # Being the assigned reviewer grants nothing by itself: an eligible reviewer is ADMIN/HR (handled above) or the
    # MANAGER of the employee's department (the department condition below). A reviewer who has since moved
    # department, left or lost the MANAGER role therefore no longer sees the review, its self-assessment or notes.
    conditions = []
    if v.employee_id is not None:
        conditions.append(PerformanceReview.employee_id == v.employee_id)
    if v.user.role == UserRole.MANAGER and v.department_id is not None:
        conditions.append(Employee.department_id == v.department_id)
    return stmt.where(or_(*conditions)) if conditions else stmt.where(false())


# --- Roles on a record ------------------------------------------------------------------------------------


def manages_employee(v: Viewer, employee: Employee) -> bool:
    """ADMIN/HR, or the MANAGER of the employee's department; never for one's own record."""
    if employee.user_id is not None and employee.user_id == v.user.id:
        return False
    if v.org_wide:
        return True
    return v.user.role == UserRole.MANAGER and v.department_id is not None and employee.department_id == v.department_id


def review_role(v: Viewer, review: PerformanceReview) -> str:
    if v.employee_id is not None and review.employee_id == v.employee_id:
        return "employee"  # always the employee view of one's own review, whatever the role
    if review.reviewer_user_id == v.user.id:
        return "reviewer"
    if v.org_wide:
        return "administrator"
    return "department_manager"


def eligible_reviewer(db: Session, user: User | None, employee: Employee) -> bool:
    """An active ADMIN/HR user, or the active MANAGER of the employee's department; never the employee."""
    if user is None or not user.is_active or user.id == employee.user_id:
        return False
    if user.role in (UserRole.ADMIN, UserRole.HR):
        return True
    if user.role != UserRole.MANAGER:
        return False
    department = managed_department_id(db.scalar(select(Employee).where(Employee.user_id == user.id)))
    return department is not None and department == employee.department_id


# --- Versioned changes ---------------------------------------------------------------------------------------

SUBJECT_COLUMN = {ReviewCycle: "cycle_id", Goal: "goal_id", PerformanceReview: "review_id"}


def apply_change(db: Session, record, actor: User, now: datetime, event_type: PerformanceEventType, values: dict[str, Any],
                 *, from_status: str | None = None, to_status: str | None = None, details: dict[str, Any] | None = None,
                 allowed_from: tuple | None = None) -> int:
    """Conditional UPDATE on (id, version[, status]) and one history event for the new version."""
    model = type(record)
    new_version = record.version + 1
    conditions = [model.id == record.id, model.version == record.version]
    if allowed_from is not None:
        conditions.append(model.status.in_(allowed_from))
    changed = db.execute(update(model).where(*conditions)
                         .values(**values, version=new_version, updated_by_user_id=actor.id, updated_at=now)
                         .execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        raise ConcurrentChange()
    db.add(PerformanceEvent(**{SUBJECT_COLUMN[model]: record.id}, version=new_version, event_type=event_type,
                            from_status=from_status, to_status=to_status, actor_user_id=actor.id, details=details,
                            created_at=now))
    db.flush()
    return new_version


def add_created_event(db: Session, record, actor: User, now: datetime, event_type: PerformanceEventType, to_status: str,
                      details: dict[str, Any] | None) -> None:
    db.add(PerformanceEvent(**{SUBJECT_COLUMN[type(record)]: record.id}, version=1, event_type=event_type,
                            to_status=to_status, actor_user_id=actor.id, details=details, created_at=now))


def unfinished_reviews(db: Session, cycle_id: int) -> int:
    return db.scalar(select(func.count(PerformanceReview.id)).where(
        PerformanceReview.cycle_id == cycle_id, PerformanceReview.status.in_(OPEN_REVIEW_STATUSES))) or 0
