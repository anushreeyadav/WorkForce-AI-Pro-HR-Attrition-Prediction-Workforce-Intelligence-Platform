"""Bounded keyword search across record types, each through its module's own visibility rules.

Every source is one query: the module's visibility helper, a literal case-insensitive keyword condition on
non-confidential label fields only, and LIMIT n+1 (to report truncation). Results carry an id, a display label,
a few permitted summary fields and the detail resource path. Never: descriptions, notes, review content or
ratings, salary values, risk scores, model features, contact details or audit metadata.

Deliberately NOT searched (use the module APIs, which apply their stricter rules): payroll, audit logs, risk alerts
(an alert found by an employee's name would reveal individual risk data), attendance, leave and datasets.

Ordering (deterministic): record type in SOURCES order, then match quality (exact label, prefix, substring), then
the label case-insensitively, then id.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.core import permissions
from app.models import (
    Department,
    Employee,
    Goal,
    Intervention,
    PerformanceReview,
    Recommendation,
    ReviewCycle,
    Task,
    User,
)
from app.services import interventions as intervention_service
from app.services import performance as performance_service
from app.services import recommendations as recommendation_service
from app.services import tasks as task_service
from app.services.employee_access import apply_scope, employee_scope
from app.services.search import LIKE_ESCAPE, contains_pattern, keyword_condition


@dataclass(frozen=True)
class Source:
    type: str
    roles: tuple
    label_column: Any  # column used for match ranking and ordering
    id_column: Any  # the record's primary key (final tie-breaker)
    match_columns: tuple  # columns the keyword is matched against
    query: Callable[[Session, User], Select]  # visible rows, selecting the model entity
    present: Callable[[Any], tuple[str, dict, datetime | None, str]]  # label, summary, timestamp, resource


def _employees(db: Session, user: User) -> Select:
    return apply_scope(select(Employee), employee_scope(db, user))


def _departments(db: Session, user: User) -> Select:
    return select(Department)


def _tasks(db: Session, user: User) -> Select:
    return task_service.visible(task_service.viewer(db, user), select(Task))


def _interventions(db: Session, user: User) -> Select:
    return intervention_service.visible(db, user, select(Intervention))


def _recommendations(db: Session, user: User) -> Select:
    return recommendation_service.visible(db, user, select(Recommendation))


def _goals(db: Session, user: User) -> Select:
    return performance_service.visible_goals(performance_service.viewer(db, user), select(Goal))


def _cycles(db: Session, user: User) -> Select:
    return performance_service.visible_cycles(performance_service.viewer(db, user), select(ReviewCycle))


def _reviews(db: Session, user: User) -> Select:
    stmt = performance_service.visible_reviews(performance_service.viewer(db, user), select(PerformanceReview))
    return stmt.join(ReviewCycle, ReviewCycle.id == PerformanceReview.cycle_id)


SOURCES: tuple[Source, ...] = (
    Source("employee", permissions.EMPLOYEE_READ, Employee.full_name, Employee.id, (Employee.full_name, Employee.employee_code),
           _employees,
           lambda e: (f"{e.full_name} ({e.employee_code})",
                      {"department_id": e.department_id, "designation": e.designation, "status": e.status.value},
                      e.updated_at, f"/api/employees/{e.id}")),
    Source("department", permissions.DEPARTMENT_READ, Department.name, Department.id, (Department.name,), _departments,
           lambda d: (d.name, {"is_active": d.is_active}, d.updated_at, f"/api/departments/{d.id}")),
    Source("task", permissions.TASK_READ, Task.title, Task.id, (Task.title,), _tasks,
           lambda t: (t.title, {"status": t.status.value, "priority": t.priority.value,
                                "due_date": t.due_date.isoformat() if t.due_date else None},
                      t.updated_at, f"/api/tasks/{t.id}")),
    Source("intervention", permissions.INTERVENTION_MANAGE, Intervention.title, Intervention.id, (Intervention.title,), _interventions,
           lambda i: (i.title, {"status": i.status.value, "priority": i.priority.value, "category": i.category.value,
                                "due_date": i.due_date.isoformat() if i.due_date else None},
                      i.updated_at, f"/api/interventions/{i.id}")),
    Source("recommendation", permissions.RECOMMENDATION_REVIEW, Recommendation.title, Recommendation.id, (Recommendation.title,),
           _recommendations,
           lambda r: (r.title, {"rule_version": r.rule_version, "category": r.category.value,
                                "priority": r.priority.value, "status": r.status.value, "scope": r.scope.value},
                      r.evaluated_at, f"/api/recommendations/{r.id}")),
    Source("goal", permissions.PERFORMANCE_READ, Goal.title, Goal.id, (Goal.title,), _goals,
           lambda g: (g.title, {"status": g.status.value, "progress_percent": g.progress_percent,
                                "target_date": g.target_date.isoformat()}, g.updated_at, f"/api/performance/goals/{g.id}")),
    Source("review_cycle", permissions.PERFORMANCE_READ, ReviewCycle.name, ReviewCycle.id, (ReviewCycle.name,), _cycles,
           lambda c: (c.name, {"status": c.status.value, "period_start": c.period_start.isoformat(),
                               "period_end": c.period_end.isoformat()}, c.updated_at, f"/api/performance/cycles/{c.id}")),
    Source("performance_review", permissions.PERFORMANCE_READ, ReviewCycle.name, PerformanceReview.id,
           (ReviewCycle.name, Employee.full_name, Employee.employee_code), _reviews,
           lambda r: (f"Performance review #{r.id}", {"cycle_id": r.cycle_id, "employee_id": r.employee_id,
                                                      "status": r.status.value},
                      r.updated_at, f"/api/performance/reviews/{r.id}")),
)
TYPES = tuple(s.type for s in SOURCES)


@dataclass
class SearchResult:
    items: list[dict]
    counts: dict[str, int]
    truncated_types: list[str]
    searched_types: list[str]
    unavailable_types: list[str]


def search(db: Session, user: User, term: str, types: set[str] | None, limit: int) -> SearchResult:
    lowered = term.lower()
    prefix = contains_pattern(term)[1:]  # drop the leading %: "term%"
    result = SearchResult([], {}, [], [], [])
    seen: set[tuple[str, int]] = set()
    for source in SOURCES:
        if types is not None and source.type not in types:
            continue
        if user.role not in source.roles:
            result.unavailable_types.append(source.type)
            continue
        result.searched_types.append(source.type)
        label = func.lower(source.label_column)
        quality = case((label == lowered, 0), (label.like(prefix.lower(), escape=LIKE_ESCAPE), 1), else_=2)
        stmt = (source.query(db, user).where(keyword_condition(term, *source.match_columns))
                .order_by(quality, label, source.id_column).limit(limit + 1))
        rows = db.scalars(stmt).unique().all()
        if len(rows) > limit:
            result.truncated_types.append(source.type)
            rows = rows[:limit]
        result.counts[source.type] = len(rows)
        for row in rows:
            if (source.type, row.id) in seen:
                continue
            seen.add((source.type, row.id))
            label_text, summary, timestamp, resource = source.present(row)
            result.items.append({"type": source.type, "id": row.id, "label": label_text, "summary": summary,
                                 "timestamp": timestamp, "resource": resource})
    return result
