"""Scoped, sanitized audit-log queries shared by GET /api/audit-logs and the audit export.

Scope:
- ADMIN/HR: every record, optionally narrowed to records ASSOCIATED with one department.
- MANAGER: only records associated with their own department, and only for entity types whose department can be
  derived reliably in SQL: employee, department, attendance, leave_request, risk_alert, intervention and
  recommendation, task, performance goal and performance review. Everything else (auth, payroll, datasets, models, monitoring runs, catalogs, exports) is excluded
  rather than guessed. Risk-alert, intervention and recommendation records about the manager are excluded too.
  Actors who are neither the manager nor linked to an employee of the department are shown as hidden.

Sanitization on read: details pass through the same redaction as at write time (covering rows written before the
policy), and the free-text justifications stored for attendance corrections and status changes are withheld.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, Select, and_, cast, exists, false, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Attendance,
    AuditLog,
    Employee,
    Goal,
    Intervention,
    LeaveRequest,
    PerformanceReview,
    Recommendation,
    RecommendationScope,
    RiskAlert,
    Task,
)
from app.services import audit_catalog
from app.services.audit import redact
from app.services.tasks import not_about_viewer

WITHHELD = "[withheld: free text]"
# (action, key): free-text justifications that are stored (documented exception) but never returned.
FREE_TEXT_FIELDS = {("attendance.corrected", "reason"), ("employee.status_changed", "reason")}
DEPARTMENT_LINKED_TYPES = ("employee", "department", "attendance", "leave_request", "risk_alert", "intervention",
                           "recommendation", "task", "performance_goal", "performance_review")


@dataclass(frozen=True)
class AuditScope:
    department_id: int | None  # None = organization (ADMIN/HR only)
    viewer_user_id: int
    restricted: bool  # True for MANAGER: department-linked entity types only, actors outside hidden


@dataclass(frozen=True)
class AuditFilters:
    start: datetime
    end: datetime
    actor_user_id: int | None = None
    action: str | None = None
    category: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    newest_first: bool = True


def _department_condition(department_id: int, exclude_user_id: int | None):
    """Records whose entity belongs to the department. `exclude_user_id` drops risk-related records about that user."""
    entity = cast(AuditLog.entity_id, Integer)  # audit entity ids are stored as text

    def employee_in_department(employee_id_column, risk_related: bool):
        condition = and_(Employee.id == employee_id_column, Employee.department_id == department_id)
        if risk_related and exclude_user_id is not None:
            condition = and_(condition, or_(Employee.user_id.is_(None), Employee.user_id != exclude_user_id))
        return condition

    return or_(
        and_(AuditLog.entity_type == "employee",
             exists(select(Employee.id).where(employee_in_department(entity, False)))),
        and_(AuditLog.entity_type == "department", AuditLog.entity_id == str(department_id)),
        and_(AuditLog.entity_type == "attendance",
             exists(select(Attendance.id).join(Employee, Employee.id == Attendance.employee_id)
                    .where(Attendance.id == entity, employee_in_department(Attendance.employee_id, False)))),
        and_(AuditLog.entity_type == "leave_request",
             exists(select(LeaveRequest.id).join(Employee, Employee.id == LeaveRequest.employee_id)
                    .where(LeaveRequest.id == entity, employee_in_department(LeaveRequest.employee_id, False)))),
        and_(AuditLog.entity_type == "risk_alert",
             exists(select(RiskAlert.id).join(Employee, Employee.id == RiskAlert.employee_id)
                    .where(RiskAlert.id == entity, employee_in_department(RiskAlert.employee_id, True)))),
        and_(AuditLog.entity_type == "intervention",
             exists(select(Intervention.id).join(Employee, Employee.id == Intervention.employee_id)
                    .where(Intervention.id == entity, employee_in_department(Intervention.employee_id, True)))),
        and_(AuditLog.entity_type == "recommendation", or_(
            exists(select(Recommendation.id).join(Employee, Employee.id == Recommendation.employee_id)
                   .where(Recommendation.id == entity, Recommendation.scope == RecommendationScope.INDIVIDUAL,
                          employee_in_department(Recommendation.employee_id, True))),
            exists(select(Recommendation.id).where(Recommendation.id == entity,
                                                   Recommendation.scope == RecommendationScope.DEPARTMENT,
                                                   Recommendation.department_id == department_id)))),
        and_(AuditLog.entity_type == "performance_goal",
             exists(select(Goal.id).join(Employee, Employee.id == Goal.employee_id)
                    .where(Goal.id == entity, employee_in_department(Goal.employee_id, True)))),
        and_(AuditLog.entity_type == "performance_review",
             exists(select(PerformanceReview.id).join(Employee, Employee.id == PerformanceReview.employee_id)
                    .where(PerformanceReview.id == entity, employee_in_department(PerformanceReview.employee_id, True)))),
        and_(AuditLog.entity_type == "task", exists(select(Task.id).where(Task.id == entity, Task.department_id == department_id,
                                                                          *_task_not_about(exclude_user_id)))),
    )


def _task_not_about(user_id: int | None) -> list:
    """Tasks linked to an intervention/recommendation about `user_id` are hidden from that user (as the tasks are)."""
    return [] if user_id is None else [not_about_viewer(user_id)]


def statement(scope: AuditScope, filters: AuditFilters) -> Select:
    stmt = select(AuditLog).where(AuditLog.created_at >= filters.start, AuditLog.created_at < filters.end)
    if scope.department_id is not None:
        stmt = stmt.where(_department_condition(scope.department_id, scope.viewer_user_id if scope.restricted else None))
    elif scope.restricted:
        stmt = stmt.where(false())  # a restricted viewer always has a department
    if filters.actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == filters.actor_user_id)
    if filters.action is not None:
        stmt = stmt.where(AuditLog.action == filters.action)
    if filters.category is not None:
        actions = [a for a, (category, _, _) in audit_catalog.ACTIONS.items() if category == filters.category]
        stmt = stmt.where(AuditLog.action.in_(actions))
    if filters.entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == filters.entity_type)
    if filters.entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == filters.entity_id)
    if filters.newest_first:
        return stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    return stmt.order_by(AuditLog.created_at.asc(), AuditLog.id.asc())


def sanitize_details(action: str, details: dict | None) -> dict | None:
    if details is None:
        return None
    cleaned = redact(details)
    for free_action, key in FREE_TEXT_FIELDS:
        if action == free_action and key in cleaned:
            cleaned[key] = WITHHELD
    return cleaned


def visible_actor_ids(db: Session, scope: AuditScope, actor_ids: set[int]) -> set[int] | None:
    """Actor ids a restricted viewer may see (themselves and users linked to employees of their department);
    None means all actors are visible."""
    if not scope.restricted:
        return None
    if not actor_ids:
        return set()
    linked = set(db.scalars(select(Employee.user_id).where(Employee.user_id.in_(actor_ids),
                                                            Employee.department_id == scope.department_id)))
    return linked | ({scope.viewer_user_id} & actor_ids)


def present(rows: list[AuditLog], visible_actors: set[int] | None) -> list[dict]:
    items = []
    for row in rows:
        hidden = visible_actors is not None and row.actor_user_id is not None and row.actor_user_id not in visible_actors
        items.append({
            "id": row.id, "created_at": row.created_at,
            "actor_user_id": None if hidden else row.actor_user_id, "actor_hidden": hidden,
            "action": row.action, "category": audit_catalog.category_of(row.action),
            "description": audit_catalog.description_of(row.action),
            "entity_type": row.entity_type, "entity_id": row.entity_id,
            "details": sanitize_details(row.action, row.details),
        })
    return items
