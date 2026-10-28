"""Performance management: review cycles, employee goals and performance reviews.

- Each endpoint commits once: the change, its history event, its audit row and its notifications succeed or fail
  together. Repeated requests that change nothing write nothing (no event, audit row or notification).
- Audit rows, history and notifications hold ids, field names and statuses only. Never review text, goal text,
  feedback, notes or reasons; ratings appear in review history only (hidden from the employee) and never in audit
  rows or notifications.
- Out-of-scope records return 404; visible records you may not change return 403; wrong state returns 409.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    Employee,
    EmploymentStatus,
    Goal,
    GoalStatus,
    PerformanceEvent,
    PerformanceEventType,
    PerformanceReview,
    ReviewCycle,
    ReviewCycleStatus,
    ReviewStatus,
    User,
)
from app.models.base import utcnow
from app.models.performance import ACTIVE_CYCLE_STATUSES, ACTIVE_GOAL_STATUSES, RATING_SCALE
from app.schemas.common import Page, PaginationParams
from app.schemas.performance import (
    MANAGER_FIELDS,
    CycleCreate,
    CycleListParams,
    CycleRead,
    CycleTransition,
    CycleUpdate,
    EventRead,
    GoalCreate,
    GoalListParams,
    GoalRead,
    GoalUpdate,
    ManagerReviewSubmit,
    PerformanceSummary,
    ReviewCancel,
    ReviewCreate,
    ReviewListParams,
    ReviewRead,
    ReviewReopen,
    ReviewUpdate,
    SelfAssessmentSubmit,
    VersionOnly,
    check_cycle_dates,
)
from app.services import performance as service
from app.services.audit import record_audit
from app.services.search import keyword_condition
from app.services.notifications import department_manager_user_ids, notify
from app.services.pagination import paginate

router = APIRouter(prefix="/api/performance", tags=["performance"])

Reader = Annotated[User, Depends(require_roles(*permissions.PERFORMANCE_READ))]
Admin = Annotated[User, Depends(require_roles(*permissions.PERFORMANCE_ADMIN))]

CONCURRENT = "The record was changed by someone else; reload it and try again"
R = ReviewStatus


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _today() -> date:
    return utcnow().astimezone(get_settings().org_tz).date()


def _check_version(record, expected: int | None) -> None:
    if expected is not None and expected != record.version:
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT)


def _notify(db: DbSession, user_ids, actor: User, title: str, message: str, entity_type: str, entity_id: int) -> None:
    """One minimal notification per distinct recipient, never to the actor."""
    for user_id in sorted({u for u in user_ids if u is not None and u != actor.id}):
        notify(db, user_id=user_id, category="performance", title=title, message=message, entity_type=entity_type,
               entity_id=entity_id)


def _apply(db: DbSession, record, actor: User, event: PerformanceEventType, values: dict, *, audit_action: str,
           entity_type: str, audit_details: dict, **kwargs) -> None:
    try:
        service.apply_change(db, record, actor, utcnow(), event, values, **kwargs)
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT) from None
    record_audit(db, actor=actor, action=audit_action, entity_type=entity_type, entity_id=record.id, details=audit_details)


def _commit(db: DbSession, conflict: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, conflict) from None


def _history(db: DbSession, column, record_id: int, params: PaginationParams, hide_ratings: bool) -> Page[EventRead]:
    stmt = select(PerformanceEvent).where(column == record_id).order_by(PerformanceEvent.version, PerformanceEvent.id)
    items, total = paginate(db, stmt, params)
    reads = []
    for event in items:
        read = EventRead.model_validate(event)
        if hide_ratings and read.details and "rating" in read.details:
            read = read.model_copy(update={"details": {k: v for k, v in read.details.items() if k != "rating"}})
        reads.append(read)
    return Page[EventRead].build(reads, total, params)


# --- Summary (static; declared before dynamic routes) --------------------------------------------------------


@router.get("/summary", response_model=PerformanceSummary)
def performance_summary(db: DbSession, user: Reader, cycle_id: Annotated[int | None, Query(gt=0)] = None) -> PerformanceSummary:
    v = service.viewer(db, user)
    cycles = db.execute(service.visible_cycles(v, select(ReviewCycle.status))).scalars().all()
    review_stmt = service.visible_reviews(v, select(PerformanceReview.status, PerformanceReview.overall_rating,
                                                    PerformanceReview.reviewer_user_id, PerformanceReview.employee_id))
    goal_stmt = service.visible_goals(v, select(Goal.status, Goal.progress_percent, Goal.target_date))
    if cycle_id is not None:
        review_stmt = review_stmt.where(PerformanceReview.cycle_id == cycle_id)
        goal_stmt = goal_stmt.where(Goal.cycle_id == cycle_id)
    reviews = db.execute(review_stmt).all()
    goals = db.execute(goal_stmt).all()
    by_review = {s.value: 0 for s in ReviewStatus}
    ratings = {str(r): 0 for r in RATING_SCALE}
    awaiting = 0
    for review_status, rating, reviewer, employee_id in reviews:
        by_review[review_status.value] += 1
        if review_status == R.FINALIZED and rating is not None and employee_id != v.employee_id:
            ratings[str(rating)] += 1
        awaiting += int(reviewer == user.id and review_status in (R.ASSIGNED, R.SELF_SUBMITTED))
        awaiting += int(employee_id == v.employee_id and review_status == R.ASSIGNED)
    by_goal = {s.value: 0 for s in GoalStatus}
    active_progress = []
    at_risk = 0
    today = _today()
    for goal_status, progress, target in goals:
        by_goal[goal_status.value] += 1
        if goal_status in ACTIVE_GOAL_STATUSES:
            active_progress.append(progress)
            at_risk += int(goal_status == GoalStatus.AT_RISK or target < today)
    by_cycle = {s.value: 0 for s in ReviewCycleStatus}
    for cycle_status in cycles:
        by_cycle[cycle_status.value] += 1
    employee_only = not v.org_wide and user.role.value == "EMPLOYEE"
    return PerformanceSummary(
        cycles_by_status=by_cycle, reviews_by_status=by_review,
        finalized_rating_distribution=None if employee_only else ratings, goals_by_status=by_goal,
        active_goals_average_progress=round(sum(active_progress) / len(active_progress), 1) if active_progress else None,
        goals_at_risk_or_overdue=at_risk, reviews_awaiting_you=awaiting,
        rating_scale={str(k): v for k, v in RATING_SCALE.items()})


# --- Cycles ---------------------------------------------------------------------------------------------------


def _load_cycle(db: DbSession, v: service.Viewer, cycle_id: int) -> ReviewCycle:
    cycle = db.scalar(service.visible_cycles(v, select(ReviewCycle)).where(ReviewCycle.id == cycle_id)
                      .execution_options(populate_existing=True))
    if cycle is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Review cycle not found")
    return cycle


@router.post("/cycles", response_model=CycleRead, status_code=status.HTTP_201_CREATED)
def create_cycle(payload: CycleCreate, db: DbSession, actor: Admin) -> ReviewCycle:
    if db.scalar(select(ReviewCycle.id).where(ReviewCycle.name == payload.name)) is not None:
        raise _error(status.HTTP_409_CONFLICT, "A review cycle with this name already exists")
    cycle = ReviewCycle(**payload.model_dump(), status=ReviewCycleStatus.DRAFT, created_by_user_id=actor.id,
                        updated_by_user_id=actor.id, version=1)
    db.add(cycle)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "A review cycle with this name already exists") from None
    dates = {k: getattr(cycle, k).isoformat() for k in ("period_start", "period_end", "self_assessment_deadline",
                                                         "manager_review_deadline")}
    service.add_created_event(db, cycle, actor, utcnow(), PerformanceEventType.CYCLE_CREATED, "DRAFT", dates)
    record_audit(db, actor=actor, action="performance.cycle_created", entity_type="performance_cycle",
                 entity_id=cycle.id, details=dates)
    _commit(db, "A review cycle with this name already exists")
    db.refresh(cycle)
    return cycle


@router.get("/cycles", response_model=Page[CycleRead])
def list_cycles(params: Annotated[CycleListParams, Query()], db: DbSession, user: Reader) -> Page[CycleRead]:
    v = service.viewer(db, user)
    stmt = service.visible_cycles(v, select(ReviewCycle))
    if params.status is not None:
        stmt = stmt.where(ReviewCycle.status == params.status)
    if params.period_from is not None:
        stmt = stmt.where(ReviewCycle.period_end >= params.period_from)
    if params.period_to is not None:
        stmt = stmt.where(ReviewCycle.period_start <= params.period_to)
    items, total = paginate(db, stmt.order_by(ReviewCycle.period_start.desc(), ReviewCycle.id.desc()), params)
    return Page[CycleRead].build([CycleRead.model_validate(c) for c in items], total, params)


@router.get("/cycles/{cycle_id}", response_model=CycleRead)
def get_cycle(cycle_id: int, db: DbSession, user: Reader) -> ReviewCycle:
    return _load_cycle(db, service.viewer(db, user), cycle_id)


@router.patch("/cycles/{cycle_id}", response_model=CycleRead)
def update_cycle(cycle_id: int, payload: CycleUpdate, db: DbSession, user: Reader) -> ReviewCycle:
    v = service.viewer(db, user)
    cycle = _load_cycle(db, v, cycle_id)
    if not v.org_wide:
        raise _error(status.HTTP_403_FORBIDDEN, "Only ADMIN and HR can configure review cycles")
    changes = {k: val for k, val in payload.model_dump(exclude_unset=True, exclude={"expected_version"}).items()
               if getattr(cycle, k) != val}
    if any(val is None for k, val in changes.items() if k != "description"):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only description can be cleared")
    if cycle.status in (ReviewCycleStatus.COMPLETED, ReviewCycleStatus.CANCELLED):
        raise _error(status.HTTP_409_CONFLICT, f"A {cycle.status.value} cycle cannot be changed")
    locked = set(changes) - {"description", "self_assessment_deadline", "manager_review_deadline"}
    if cycle.status != ReviewCycleStatus.DRAFT and locked:
        raise _error(status.HTTP_409_CONFLICT, "Only the description and deadlines can change once a cycle is open")
    _check_version(cycle, payload.expected_version)
    if not changes:
        return cycle
    merged = {k: changes.get(k, getattr(cycle, k)) for k in ("period_start", "period_end", "self_assessment_deadline",
                                                             "manager_review_deadline")}
    try:
        check_cycle_dates(**{"period_start": merged["period_start"], "period_end": merged["period_end"],
                             "self_deadline": merged["self_assessment_deadline"],
                             "manager_deadline": merged["manager_review_deadline"]})
    except ValueError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    fields = sorted(changes)
    _apply(db, cycle, user, PerformanceEventType.CYCLE_UPDATED, changes, audit_action="performance.cycle_updated",
           entity_type="performance_cycle", audit_details={"fields": fields}, details={"fields": fields})
    _commit(db, "A review cycle with this name already exists")
    return _load_cycle(db, v, cycle.id)


@router.post("/cycles/{cycle_id}/transition", response_model=CycleRead)
def transition_cycle(cycle_id: int, payload: CycleTransition, db: DbSession, user: Reader) -> ReviewCycle:
    v = service.viewer(db, user)
    cycle = _load_cycle(db, v, cycle_id)
    if not v.org_wide:
        raise _error(status.HTTP_403_FORBIDDEN, "Only ADMIN and HR can change a cycle's status")
    rule = service.CYCLE_TRANSITIONS[payload.action]
    if cycle.status == rule.target:
        return cycle  # idempotent repeat
    if cycle.status not in rule.allowed_from:
        raise _error(status.HTTP_409_CONFLICT, f"Cannot {payload.action} a {cycle.status.value} cycle")
    _check_version(cycle, payload.expected_version)
    if payload.action == "complete" and (pending := service.unfinished_reviews(db, cycle.id)):
        raise _error(status.HTTP_409_CONFLICT, f"{pending} review(s) are not finalized or cancelled yet")
    values: dict[str, Any] = {"status": rule.target}
    if payload.action == "cancel":
        values["cancellation_reason"] = payload.cancellation_reason
    action = {"open": "opened", "start_reviews": "reviews_started", "complete": "completed", "cancel": "cancelled"}
    _apply(db, cycle, user, rule.event, values, audit_action=f"performance.cycle_{action[payload.action]}",
           entity_type="performance_cycle",
           audit_details={"from_status": cycle.status.value, "to_status": rule.target.value},
           from_status=cycle.status.value, to_status=rule.target.value, allowed_from=rule.allowed_from)
    db.commit()
    return _load_cycle(db, v, cycle.id)


# --- Goals ----------------------------------------------------------------------------------------------------


def _goal_read(goal: Goal, v: service.Viewer) -> GoalRead:
    overdue = goal.status in ACTIVE_GOAL_STATUSES and goal.target_date < _today()
    return GoalRead.model_validate(goal).model_copy(update={"is_overdue": overdue,
                                                            "can_manage": service.manages_employee(v, goal.employee)})


def _load_goal(db: DbSession, v: service.Viewer, goal_id: int) -> Goal:
    goal = db.scalar(service.visible_goals(v, select(Goal).options(selectinload(Goal.employee)))
                     .where(Goal.id == goal_id).execution_options(populate_existing=True))
    if goal is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Goal not found")
    return goal


def _usable_cycle(db: DbSession, v: service.Viewer, cycle_id: int | None) -> None:
    if cycle_id is None:
        return
    cycle = db.get(ReviewCycle, cycle_id)
    if cycle is None or (not v.org_wide and cycle.status == ReviewCycleStatus.DRAFT):
        raise _error(status.HTTP_404_NOT_FOUND, "Review cycle not found")
    if cycle.status in (ReviewCycleStatus.COMPLETED, ReviewCycleStatus.CANCELLED):
        raise _error(status.HTTP_409_CONFLICT, f"Goals cannot be added to a {cycle.status.value} cycle")


@router.post("/goals", response_model=GoalRead, status_code=status.HTTP_201_CREATED)
def create_goal(payload: GoalCreate, db: DbSession, user: Reader) -> GoalRead:
    v = service.viewer(db, user)
    employee_id = payload.employee_id or v.employee_id
    if employee_id is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Your account has no employee record; give employee_id")
    employee = db.get(Employee, employee_id)
    own = employee is not None and employee.id == v.employee_id
    if employee is None or not (own or service.manages_employee(v, employee)):
        raise _error(status.HTTP_404_NOT_FOUND, "Employee not found")
    if employee.status == EmploymentStatus.INACTIVE:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Goals cannot be set for inactive employees")
    _usable_cycle(db, v, payload.cycle_id)
    goal = Goal(**payload.model_dump(exclude={"employee_id"}), employee_id=employee.id,
                status=GoalStatus.IN_PROGRESS if payload.progress_percent > 0 else GoalStatus.NOT_STARTED,
                created_by_user_id=user.id, updated_by_user_id=user.id, version=1)
    db.add(goal)
    db.flush()
    details = {"employee_id": employee.id, "cycle_id": payload.cycle_id, "category": payload.category.value}
    service.add_created_event(db, goal, user, utcnow(), PerformanceEventType.GOAL_CREATED, goal.status.value, details)
    record_audit(db, actor=user, action="performance.goal_created", entity_type="performance_goal", entity_id=goal.id,
                 details=details)
    if not own:
        _notify(db, [employee.user_id], user, "New goal", f"Goal #{goal.id} has been set for you.", "performance_goal",
                goal.id)
    db.commit()
    return _goal_read(_load_goal(db, v, goal.id), v)


@router.get("/goals", response_model=Page[GoalRead])
def list_goals(params: Annotated[GoalListParams, Query()], db: DbSession, user: Reader) -> Page[GoalRead]:
    v = service.viewer(db, user)
    stmt = service.visible_goals(v, select(Goal).options(selectinload(Goal.employee)))
    for value, column in ((params.employee_id, Goal.employee_id), (params.department_id, Employee.department_id),
                          (params.cycle_id, Goal.cycle_id), (params.status, Goal.status), (params.category, Goal.category)):
        if value is not None:
            stmt = stmt.where(column == value)
    if params.mine:
        stmt = stmt.where(Goal.employee_id == v.employee_id) if v.employee_id else stmt.where(Goal.id.is_(None))
    if params.q:
        stmt = stmt.where(keyword_condition(params.q, Goal.title))
    if params.target_from:
        stmt = stmt.where(Goal.target_date >= params.target_from)
    if params.target_to:
        stmt = stmt.where(Goal.target_date <= params.target_to)
    items, total = paginate(db, stmt.order_by(Goal.target_date.asc(), Goal.id.asc()), params)
    return Page[GoalRead].build([_goal_read(g, v) for g in items], total, params)


@router.get("/goals/{goal_id}", response_model=GoalRead)
def get_goal(goal_id: int, db: DbSession, user: Reader) -> GoalRead:
    v = service.viewer(db, user)
    return _goal_read(_load_goal(db, v, goal_id), v)


@router.get("/goals/{goal_id}/history", response_model=Page[EventRead])
def goal_history(goal_id: int, params: Annotated[PaginationParams, Query()], db: DbSession, user: Reader) -> Page[EventRead]:
    goal = _load_goal(db, service.viewer(db, user), goal_id)
    return _history(db, PerformanceEvent.goal_id, goal.id, params, hide_ratings=False)


MANAGER_ONLY_GOAL_FIELDS = {"manager_feedback", "employee_id"}
# Status changes made by the goal's owner that the employee's managers are told about.
SIGNIFICANT_GOAL_STATUSES = (GoalStatus.AT_RISK, GoalStatus.COMPLETED)


@router.patch("/goals/{goal_id}", response_model=GoalRead)
def update_goal(goal_id: int, payload: GoalUpdate, db: DbSession, user: Reader) -> GoalRead:
    v = service.viewer(db, user)
    goal = _load_goal(db, v, goal_id)
    manager = service.manages_employee(v, goal.employee)
    owner = goal.employee_id == v.employee_id
    if not (manager or owner):
        raise _error(status.HTTP_403_FORBIDDEN, "You cannot change this goal")
    changes = {k: val for k, val in payload.model_dump(exclude_unset=True, exclude={"expected_version"}).items()
               if getattr(goal, k) != val}
    if not manager:
        if set(changes) & MANAGER_ONLY_GOAL_FIELDS or changes.get("status") == GoalStatus.CANCELLED:
            raise _error(status.HTTP_403_FORBIDDEN, "Only the employee's manager, HR or ADMIN can do this")
        if goal.status not in ACTIVE_GOAL_STATUSES and changes:
            raise _error(status.HTTP_409_CONFLICT, f"A {goal.status.value} goal can only be changed by a manager")
    _check_version(goal, payload.expected_version)
    if not changes:
        return _goal_read(goal, v)
    if "employee_id" in changes:
        new_owner = db.get(Employee, changes["employee_id"])
        if new_owner is None or not service.manages_employee(v, new_owner):
            raise _error(status.HTTP_404_NOT_FOUND, "Employee not found")
        if new_owner.status == EmploymentStatus.INACTIVE:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Goals cannot be assigned to inactive employees")
    start = changes.get("start_date", goal.start_date)
    target = changes.get("target_date", goal.target_date)
    if start > target:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "start_date must be on or before target_date")
    new_status = changes.get("status", goal.status)
    progress = changes.get("progress_percent", goal.progress_percent)
    if new_status == GoalStatus.COMPLETED and progress != 100:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "A goal can be completed only at 100% progress")
    if "status" in changes:
        changes["completed_at"] = utcnow() if new_status == GoalStatus.COMPLETED else None
    fields = sorted(k for k in changes if k != "completed_at")
    details: dict[str, Any] = {"fields": fields}
    if "status" in changes:
        details["status"] = {"from": goal.status.value, "to": new_status.value}
    if "progress_percent" in changes:
        details["progress"] = {"from": goal.progress_percent, "to": progress}
    if "employee_id" in changes:
        details["employee"] = {"from": goal.employee_id, "to": changes["employee_id"]}
    event = (PerformanceEventType.GOAL_REASSIGNED if "employee_id" in changes else
             PerformanceEventType.GOAL_STATUS_CHANGED if "status" in changes else
             PerformanceEventType.GOAL_PROGRESS_CHANGED if "progress_percent" in changes else
             PerformanceEventType.GOAL_UPDATED)
    _apply(db, goal, user, event, changes, audit_action="performance.goal_updated", entity_type="performance_goal",
           audit_details=details, details=details, from_status=goal.status.value, to_status=new_status.value)
    if "employee_id" in changes:
        _notify(db, [db.scalar(select(Employee.user_id).where(Employee.id == changes["employee_id"]))], user,
                "New goal", f"Goal #{goal.id} has been assigned to you.", "performance_goal", goal.id)
    elif owner and "status" in changes and new_status in SIGNIFICANT_GOAL_STATUSES:
        _notify(db, department_manager_user_ids(db, goal.employee.department_id), user, "Goal status changed",
                f"Goal #{goal.id} is now {new_status.value}.", "performance_goal", goal.id)
    elif not owner and ("status" in changes or "manager_feedback" in changes):
        _notify(db, [goal.employee.user_id], user, "Goal updated",
                f"Goal #{goal.id} was updated by your manager (status {new_status.value}).", "performance_goal", goal.id)
    db.commit()
    return _goal_read(_load_goal(db, v, goal.id), v)


# --- Reviews --------------------------------------------------------------------------------------------------


def _review_read(review: PerformanceReview, v: service.Viewer) -> ReviewRead:
    role = service.review_role(v, review)
    show_evaluation = role != "employee" or review.status == R.FINALIZED
    show_private = role in ("reviewer", "administrator")
    show_self = role == "employee" or review.self_submitted_at is not None

    def evaluation(value):
        return value if show_evaluation else None

    return ReviewRead(
        id=review.id, cycle_id=review.cycle_id, employee=review.employee, reviewer_user_id=review.reviewer_user_id,
        status=review.status, self_assessment=review.self_assessment if show_self else None,
        self_submitted_at=review.self_submitted_at, manager_evaluation_visible=show_evaluation,
        manager_summary=evaluation(review.manager_summary), strengths=evaluation(review.strengths),
        improvement_areas=evaluation(review.improvement_areas), development_goals=evaluation(review.development_goals),
        training_recommendations=evaluation(review.training_recommendations),
        manager_private_notes=review.manager_private_notes if show_private else None,
        overall_rating=evaluation(review.overall_rating),
        rating_label=RATING_SCALE.get(review.overall_rating) if show_evaluation and review.overall_rating else None,
        manager_submitted_at=review.manager_submitted_at, finalized_at=review.finalized_at,
        cancelled_at=review.cancelled_at, cancellation_reason=review.cancellation_reason if role != "employee" else None,
        reopen_reason=review.reopen_reason if role != "employee" else None, version=review.version,
        created_at=review.created_at, updated_at=review.updated_at, your_role=role)


def _load_review(db: DbSession, v: service.Viewer, review_id: int) -> PerformanceReview:
    review = db.scalar(service.visible_reviews(v, select(PerformanceReview).options(
        selectinload(PerformanceReview.employee), selectinload(PerformanceReview.cycle)))
        .where(PerformanceReview.id == review_id).execution_options(populate_existing=True))
    if review is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Review not found")
    return review


def _active_cycle(review: PerformanceReview) -> None:
    if review.cycle.status not in ACTIVE_CYCLE_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, f"The review cycle is {review.cycle.status.value}; the review is read-only")


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise _error(status.HTTP_403_FORBIDDEN, detail)


@router.post("/reviews", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
def create_review(payload: ReviewCreate, db: DbSession, user: Reader) -> ReviewRead:
    v = service.viewer(db, user)
    employee = db.get(Employee, payload.employee_id)
    if employee is None or not (service.manages_employee(v, employee) or employee.id == v.employee_id):
        raise _error(status.HTTP_404_NOT_FOUND, "Employee not found")
    _require(employee.id != v.employee_id, "You cannot create your own review")
    cycle = db.get(ReviewCycle, payload.cycle_id)
    if cycle is None or (not v.org_wide and cycle.status == ReviewCycleStatus.DRAFT):
        raise _error(status.HTTP_404_NOT_FOUND, "Review cycle not found")
    if cycle.status not in ACTIVE_CYCLE_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, "Reviews can only be created in an OPEN or IN_PROGRESS cycle")
    if employee.status == EmploymentStatus.INACTIVE:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Inactive employees cannot be reviewed")
    if not service.eligible_reviewer(db, db.get(User, payload.reviewer_user_id), employee):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "This user cannot review this employee")
    duplicate = "This employee already has a review in this cycle"
    if db.scalar(select(PerformanceReview.id).where(PerformanceReview.cycle_id == cycle.id,
                                                    PerformanceReview.employee_id == employee.id)) is not None:
        raise _error(status.HTTP_409_CONFLICT, duplicate)
    review = PerformanceReview(cycle_id=cycle.id, employee_id=employee.id, reviewer_user_id=payload.reviewer_user_id,
                               status=R.ASSIGNED, created_by_user_id=user.id, updated_by_user_id=user.id, version=1)
    db.add(review)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, duplicate) from None
    details = {"cycle_id": cycle.id, "employee_id": employee.id, "reviewer_user_id": payload.reviewer_user_id}
    service.add_created_event(db, review, user, utcnow(), PerformanceEventType.REVIEW_CREATED, R.ASSIGNED.value, details)
    record_audit(db, actor=user, action="performance.review_created", entity_type="performance_review",
                 entity_id=review.id, details=details)
    _notify(db, [employee.user_id], user, "Performance review opened",
            f"Performance review #{review.id} has been opened for you.", "performance_review", review.id)
    _notify(db, [payload.reviewer_user_id], user, "Performance review assigned to you",
            f"You are the reviewer for performance review #{review.id}.", "performance_review", review.id)
    _commit(db, duplicate)
    return _review_read(_load_review(db, v, review.id), v)


@router.get("/reviews", response_model=Page[ReviewRead])
def list_reviews(params: Annotated[ReviewListParams, Query()], db: DbSession, user: Reader) -> Page[ReviewRead]:
    v = service.viewer(db, user)
    stmt = service.visible_reviews(v, select(PerformanceReview).options(selectinload(PerformanceReview.employee)))
    for value, column in ((params.cycle_id, PerformanceReview.cycle_id), (params.employee_id, PerformanceReview.employee_id),
                          (params.reviewer_user_id, PerformanceReview.reviewer_user_id),
                          (params.department_id, Employee.department_id), (params.status, PerformanceReview.status)):
        if value is not None:
            stmt = stmt.where(column == value)
    if params.to_review:
        stmt = stmt.where(PerformanceReview.reviewer_user_id == user.id)
    if params.mine:
        stmt = stmt.where(PerformanceReview.employee_id == v.employee_id) if v.employee_id else stmt.where(
            PerformanceReview.id.is_(None))
    stmt = stmt.order_by(PerformanceReview.updated_at.desc(), PerformanceReview.id.desc())
    items, total = paginate(db, stmt, params)
    return Page[ReviewRead].build([_review_read(r, v) for r in items], total, params)


@router.get("/reviews/{review_id}", response_model=ReviewRead)
def get_review(review_id: int, db: DbSession, user: Reader) -> ReviewRead:
    v = service.viewer(db, user)
    return _review_read(_load_review(db, v, review_id), v)


@router.get("/reviews/{review_id}/history", response_model=Page[EventRead])
def review_history(review_id: int, params: Annotated[PaginationParams, Query()], db: DbSession, user: Reader) -> Page[EventRead]:
    v = service.viewer(db, user)
    review = _load_review(db, v, review_id)
    return _history(db, PerformanceEvent.review_id, review.id, params,
                    hide_ratings=service.review_role(v, review) == "employee")


@router.patch("/reviews/{review_id}", response_model=ReviewRead)
def update_review(review_id: int, payload: ReviewUpdate, db: DbSession, user: Reader) -> ReviewRead:
    v = service.viewer(db, user)
    review = _load_review(db, v, review_id)
    role = service.review_role(v, review)
    given = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    changes = {k: val for k, val in given.items() if getattr(review, k) != val}
    today = _today()
    if "reviewer_user_id" in given:
        _require(role != "employee" and service.manages_employee(v, review.employee),
                 "Only ADMIN/HR or the employee's manager can reassign the reviewer")
        if len(given) > 1:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Reassign the reviewer in a request of its own")
    if "self_assessment" in given:
        _require(role == "employee", "Only the reviewed employee can write the self-assessment")
    if set(given) & set(MANAGER_FIELDS):
        _require(role == "reviewer", "Only the assigned reviewer can write the evaluation")
    _check_version(review, payload.expected_version)
    if not changes:
        return _review_read(review, v)
    _active_cycle(review)
    if "reviewer_user_id" in changes:
        if review.status not in (R.ASSIGNED, R.SELF_SUBMITTED):
            raise _error(status.HTTP_409_CONFLICT, f"A {review.status.value} review cannot be reassigned")
        if not service.eligible_reviewer(db, db.get(User, changes["reviewer_user_id"]), review.employee):
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "This user cannot review this employee")
        details = {"from_user_id": review.reviewer_user_id, "to_user_id": changes["reviewer_user_id"]}
        _apply(db, review, user, PerformanceEventType.REVIEW_REASSIGNED, changes,
               audit_action="performance.review_reassigned", entity_type="performance_review", audit_details=details,
               details=details, allowed_from=(R.ASSIGNED, R.SELF_SUBMITTED))
        _notify(db, [changes["reviewer_user_id"]], user, "Performance review assigned to you",
                f"You are the reviewer for performance review #{review.id}.", "performance_review", review.id)
        db.commit()
        return _review_read(_load_review(db, v, review.id), v)
    if "self_assessment" in changes:
        if review.status != R.ASSIGNED:
            raise _error(status.HTTP_409_CONFLICT, "The self-assessment has already been submitted")
        if today > review.cycle.self_assessment_deadline:
            raise _error(status.HTTP_409_CONFLICT, "The self-assessment deadline has passed")
        allowed: tuple = (R.ASSIGNED,)
    else:
        if review.status not in (R.ASSIGNED, R.SELF_SUBMITTED):
            raise _error(status.HTTP_409_CONFLICT, f"A {review.status.value} review evaluation cannot be edited")
        if today > review.cycle.manager_review_deadline:
            raise _error(status.HTTP_409_CONFLICT, "The manager review deadline has passed")
        allowed = (R.ASSIGNED, R.SELF_SUBMITTED)
    fields = sorted(changes)
    details = {"fields": fields}
    if "overall_rating" in changes:
        details["rating"] = {"from": review.overall_rating, "to": changes["overall_rating"]}
    _apply(db, review, user, PerformanceEventType.REVIEW_UPDATED, changes, audit_action="performance.review_updated",
           entity_type="performance_review",
           audit_details={"fields": fields, "rating_changed": "overall_rating" in changes},
           details=details, allowed_from=allowed)
    db.commit()
    return _review_read(_load_review(db, v, review.id), v)


@router.post("/reviews/{review_id}/submit-self-assessment", response_model=ReviewRead)
def submit_self_assessment(review_id: int, payload: SelfAssessmentSubmit, db: DbSession, user: Reader) -> ReviewRead:
    v = service.viewer(db, user)
    review = _load_review(db, v, review_id)
    _require(service.review_role(v, review) == "employee", "Only the reviewed employee can submit the self-assessment")
    if review.self_submitted_at is not None and review.status != R.ASSIGNED:
        return _review_read(review, v)  # already submitted: no-op
    _active_cycle(review)
    if review.status != R.ASSIGNED:
        raise _error(status.HTTP_409_CONFLICT, f"A {review.status.value} review cannot take a self-assessment")
    if _today() > review.cycle.self_assessment_deadline:
        raise _error(status.HTTP_409_CONFLICT, "The self-assessment deadline has passed")
    _check_version(review, payload.expected_version)
    text = payload.self_assessment or review.self_assessment
    if not text:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Write a self-assessment before submitting it")
    now = utcnow()
    _apply(db, review, user, PerformanceEventType.SELF_ASSESSMENT_SUBMITTED,
           {"self_assessment": text, "self_submitted_at": now, "status": R.SELF_SUBMITTED},
           audit_action="performance.self_assessment_submitted", entity_type="performance_review",
           audit_details={"from_status": "ASSIGNED", "to_status": "SELF_SUBMITTED"},
           from_status="ASSIGNED", to_status="SELF_SUBMITTED", allowed_from=(R.ASSIGNED,))
    _notify(db, [review.reviewer_user_id], user, "Self-assessment submitted",
            f"The self-assessment for performance review #{review.id} is ready for your review.", "performance_review",
            review.id)
    db.commit()
    return _review_read(_load_review(db, v, review.id), v)


@router.post("/reviews/{review_id}/submit-manager-review", response_model=ReviewRead)
def submit_manager_review(review_id: int, payload: ManagerReviewSubmit, db: DbSession, user: Reader) -> ReviewRead:
    v = service.viewer(db, user)
    review = _load_review(db, v, review_id)
    _require(service.review_role(v, review) == "reviewer", "Only the assigned reviewer can submit the evaluation")
    given = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if review.status == R.MANAGER_SUBMITTED and all(getattr(review, k) == val for k, val in given.items()):
        return _review_read(review, v)  # repeat: no-op
    _active_cycle(review)
    if review.status not in (R.ASSIGNED, R.SELF_SUBMITTED):
        raise _error(status.HTTP_409_CONFLICT, f"A {review.status.value} review cannot be submitted")
    today = _today()
    if review.status == R.ASSIGNED and today <= review.cycle.self_assessment_deadline:
        raise _error(status.HTTP_409_CONFLICT, "Wait for the self-assessment or for its deadline to pass")
    if today > review.cycle.manager_review_deadline:
        raise _error(status.HTTP_409_CONFLICT, "The manager review deadline has passed")
    _check_version(review, payload.expected_version)
    merged = {k: given.get(k, getattr(review, k)) for k in MANAGER_FIELDS}
    if merged["overall_rating"] is None or not merged["manager_summary"]:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "An overall rating and a manager summary are required")
    previous = review.status.value
    _apply(db, review, user, PerformanceEventType.MANAGER_REVIEW_SUBMITTED,
           {**given, "manager_submitted_at": utcnow(), "status": R.MANAGER_SUBMITTED},
           audit_action="performance.manager_review_submitted", entity_type="performance_review",
           audit_details={"from_status": previous, "to_status": "MANAGER_SUBMITTED",
                          "self_assessment_submitted": review.self_submitted_at is not None},
           details={"rating": merged["overall_rating"]}, from_status=previous, to_status="MANAGER_SUBMITTED",
           allowed_from=(R.ASSIGNED, R.SELF_SUBMITTED))
    if review.created_by_user_id != review.employee.user_id:
        _notify(db, [review.created_by_user_id], user, "Manager review submitted",
                f"Performance review #{review.id} is ready to be finalized.", "performance_review", review.id)
    db.commit()
    return _review_read(_load_review(db, v, review.id), v)


def _admin_action(v: service.Viewer, review: PerformanceReview, verb: str) -> None:
    _require(v.org_wide and service.review_role(v, review) != "employee", f"Only ADMIN or HR can {verb} a review "
             "(never their own)")


@router.post("/reviews/{review_id}/finalize", response_model=ReviewRead)
def finalize_review(review_id: int, payload: VersionOnly, db: DbSession, user: Reader) -> ReviewRead:
    v = service.viewer(db, user)
    review = _load_review(db, v, review_id)
    _admin_action(v, review, "finalize")
    if review.status == R.FINALIZED:
        return _review_read(review, v)  # repeat: no-op
    _active_cycle(review)
    if review.status != R.MANAGER_SUBMITTED:
        raise _error(status.HTTP_409_CONFLICT, f"A {review.status.value} review cannot be finalized")
    _check_version(review, payload.expected_version)
    _apply(db, review, user, PerformanceEventType.REVIEW_FINALIZED,
           {"status": R.FINALIZED, "finalized_at": utcnow(), "finalized_by_user_id": user.id},
           audit_action="performance.review_finalized", entity_type="performance_review",
           audit_details={"from_status": "MANAGER_SUBMITTED", "to_status": "FINALIZED"},
           from_status="MANAGER_SUBMITTED", to_status="FINALIZED", allowed_from=(R.MANAGER_SUBMITTED,))
    _notify(db, [review.employee.user_id, review.reviewer_user_id], user, "Performance review finalized",
            f"Performance review #{review.id} has been finalized.", "performance_review", review.id)
    db.commit()
    return _review_read(_load_review(db, v, review.id), v)


@router.post("/reviews/{review_id}/reopen", response_model=ReviewRead)
def reopen_review(review_id: int, payload: ReviewReopen, db: DbSession, user: Reader) -> ReviewRead:
    """Authorized correction: FINALIZED back to the reviewer, keeping the self-assessment. History keeps the earlier
    events; the reason is stored on the review (never in audit rows)."""
    v = service.viewer(db, user)
    review = _load_review(db, v, review_id)
    _admin_action(v, review, "reopen")
    if review.status != R.FINALIZED:
        raise _error(status.HTTP_409_CONFLICT, "Only a FINALIZED review can be reopened for correction")
    _active_cycle(review)
    _check_version(review, payload.expected_version)
    target = R.SELF_SUBMITTED if review.self_submitted_at is not None else R.ASSIGNED
    _apply(db, review, user, PerformanceEventType.REVIEW_REOPENED,
           {"status": target, "finalized_at": None, "finalized_by_user_id": None, "manager_submitted_at": None,
            "reopen_reason": payload.reason},
           audit_action="performance.review_reopened", entity_type="performance_review",
           audit_details={"from_status": "FINALIZED", "to_status": target.value},
           from_status="FINALIZED", to_status=target.value, allowed_from=(R.FINALIZED,))
    _notify(db, [review.reviewer_user_id], user, "Performance review reopened",
            f"Performance review #{review.id} was reopened for correction.", "performance_review", review.id)
    db.commit()
    return _review_read(_load_review(db, v, review.id), v)


@router.post("/reviews/{review_id}/cancel", response_model=ReviewRead)
def cancel_review(review_id: int, payload: ReviewCancel, db: DbSession, user: Reader) -> ReviewRead:
    v = service.viewer(db, user)
    review = _load_review(db, v, review_id)
    _admin_action(v, review, "cancel")
    if review.status == R.CANCELLED:
        return _review_read(review, v)  # repeat: no-op
    _active_cycle(review)
    if review.status == R.FINALIZED:
        raise _error(status.HTTP_409_CONFLICT, "A FINALIZED review cannot be cancelled; reopen it first")
    _check_version(review, payload.expected_version)
    previous = review.status.value
    _apply(db, review, user, PerformanceEventType.REVIEW_CANCELLED,
           {"status": R.CANCELLED, "cancelled_at": utcnow(), "cancellation_reason": payload.reason},
           audit_action="performance.review_cancelled", entity_type="performance_review",
           audit_details={"from_status": previous, "to_status": "CANCELLED"},
           from_status=previous, to_status="CANCELLED", allowed_from=(R.ASSIGNED, R.SELF_SUBMITTED, R.MANAGER_SUBMITTED))
    _notify(db, [review.reviewer_user_id], user, "Performance review cancelled",
            f"Performance review #{review.id} was cancelled.", "performance_review", review.id)
    db.commit()
    return _review_read(_load_review(db, v, review.id), v)
