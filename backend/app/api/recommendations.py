"""Rule-based workforce recommendations (advisory, human-reviewed).

Evaluation reads existing records only. Decisions never create interventions automatically, never change
risk alerts and never touch employee data. Each endpoint commits once with its audit row(s).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import (
    Department,
    Intervention,
    InterventionCategory,
    InterventionPriority,
    AuditLog,
    Recommendation,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationScope,
    RecommendationStatus,
    Task,
    TaskStatus,
    User,
    UserRole,
)
from app.models.base import utcnow
from app.schemas.common import Page
from app.schemas.recommendation import (
    CompleteRequest,
    CreateInterventionRequest,
    DismissRequest,
    EvaluateRequest,
    EvaluateResult,
    HistoryEvent,
    LinkTaskRequest,
    ListParams,
    RankedCandidate,
    RecommendationHistory,
    RecommendationRead,
    SkippedRule,
    Summary,
    VersionedAction,
)
from app.services import audit_query
from app.services import interventions as intervention_service
from app.services import recommendation_engine as engine
from app.services import recommendations as service
from app.services import tasks as task_service
from app.services.audit import record_audit
from app.services.employee_access import employee_scope
from app.services.notifications import department_manager_user_ids, notify
from app.services.pagination import paginate

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

Reviewer = Annotated[User, Depends(require_roles(*permissions.RECOMMENDATION_REVIEW))]

NOT_FOUND = "Recommendation not found"
CONCURRENT = "The recommendation was changed by someone else; reload it and try again"
# Interventions have no CRITICAL priority; a CRITICAL (rules-v2) recommendation becomes a HIGH intervention.
INTERVENTION_PRIORITY = {RecommendationPriority.LOW: InterventionPriority.LOW,
                         RecommendationPriority.MEDIUM: InterventionPriority.MEDIUM,
                         RecommendationPriority.HIGH: InterventionPriority.HIGH,
                         RecommendationPriority.CRITICAL: InterventionPriority.HIGH}
INTERVENTION_CATEGORY = {
    RecommendationCategory.SUPPORT_CHECK_IN: InterventionCategory.MANAGER_CHECK_IN,
    RecommendationCategory.WORKLOAD_REVIEW: InterventionCategory.WORKLOAD_REVIEW,
    RecommendationCategory.CAREER_DEVELOPMENT: InterventionCategory.CAREER_DEVELOPMENT,
}


def _error(code: int, detail) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _today():
    return utcnow().astimezone(get_settings().org_tz).date()


def _load(db: DbSession, user: User, rec_id: int) -> Recommendation:
    rec = db.scalar(service.visible(db, user, select(Recommendation)).where(Recommendation.id == rec_id)
                    .execution_options(populate_existing=True))
    if rec is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return rec


def _check_version(rec: Recommendation, expected: int | None) -> None:
    if expected is not None and expected != rec.version:
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT)


def _decide(db: DbSession, actor: User, rec: Recommendation, action: str, allowed_from, values: dict) -> RecommendationRead:
    try:
        service.change(db, rec, allowed_from, {**values, "reviewed_by_user_id": actor.id, "reviewed_at": utcnow()})
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT) from None
    record_audit(db, actor=actor, action=f"recommendation.{action}", entity_type="recommendation", entity_id=rec.id,
                 details={"rule_id": rec.rule_id, "rule_version": rec.rule_version, "from_status": rec.status.value,
                          "to_status": values["status"].value, "scope": rec.scope.value})
    db.commit()
    return RecommendationRead.model_validate(_load(db, actor, rec.id))


# --- Static routes -----------------------------------------------------------------------------


@router.get("/summary", response_model=Summary)
def summary(db: DbSession, user: Reviewer) -> Summary:
    rows = db.execute(service.visible(db, user, select(Recommendation)).with_only_columns(
        Recommendation.status, Recommendation.category, Recommendation.priority, Recommendation.scope)).all()
    by_status = {s.value: 0 for s in RecommendationStatus}
    by_category: dict[str, int] = {}
    by_priority = {p.value: 0 for p in RecommendationPriority}
    by_scope: dict[str, int] = {}
    for rec_status, category, priority, scope in rows:
        by_status[rec_status.value] += 1
        if rec_status in service.ACTIVE:
            by_category[category.value] = by_category.get(category.value, 0) + 1
            by_priority[priority.value] += 1
            by_scope[scope.value] = by_scope.get(scope.value, 0) + 1
    return Summary(by_status=by_status, active_by_category=by_category, active_by_priority=by_priority, active_by_scope=by_scope)


@router.post("/evaluate", response_model=EvaluateResult)
def evaluate(payload: EvaluateRequest, db: DbSession, actor: Reviewer) -> EvaluateResult:
    """Apply the deterministic rules to existing records in the caller's scope. Re-running is safe: the same
    evidence never creates a second recommendation. No model is trained or called."""
    settings = get_settings()
    department_id = payload.department_id
    if actor.role == UserRole.MANAGER:
        own = employee_scope(db, actor).department_id
        if own is None:
            raise _error(status.HTTP_403_FORBIDDEN, "Your account is not linked to a department")
        if department_id is not None and department_id != own:
            raise _error(status.HTTP_404_NOT_FOUND, "Department not found")
        department_id = own
    elif department_id is not None and db.get(Department, department_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Department not found")

    limit = min(payload.max_alerts, settings.recommendation_max_alerts_per_evaluation)
    now = utcnow()
    try:
        result = engine.STRATEGIES[payload.strategy](db, settings, actor, now, department_id, payload.include_individual, limit)
        details = {"rule_version": result.rule_version, "department_id": department_id,
                   "created": len(result.created), "already_recorded": result.already_recorded,
                   "not_actionable": len(result.outcomes), "alerts_examined": result.alerts_examined,
                   "recommendation_ids": [r.id for r in result.created][:1000]}
        if payload.strategy == "rules-v2":
            details.update(superseded=len(result.superseded), being_addressed=result.being_addressed,
                           truncated_sources=result.truncated_sources)
            _record_v2_changes(db, actor, result)
        record_audit(db, actor=actor, action="recommendation.evaluated", entity_type="recommendation", entity_id="evaluation",
                     details=details)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "Another evaluation recorded these recommendations first; retry") from None
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "A recommendation changed during evaluation; retry") from None
    return EvaluateResult(
        rule_version=result.rule_version, created=len(result.created), already_recorded=result.already_recorded,
        alerts_examined=result.alerts_examined, aggregate_subjects_examined=result.subjects_examined,
        not_actionable=[SkippedRule(rule_id=o.rule_id, status=o.status, reason=o.reason, subject=o.subject) for o in result.outcomes],
        recommendation_ids=[r.id for r in result.created], being_addressed=result.being_addressed,
        superseded=len(result.superseded), truncated_sources=result.truncated_sources, ranked=_ranked(db, result))


def _record_v2_changes(db: DbSession, actor: User, result: engine.EngineResult) -> None:
    """One audit row per generated or superseded recommendation, and a notification to the department's managers
    for each newly CRITICAL individual recommendation (never the actor or the employee concerned)."""
    for rec in result.created:
        record_audit(db, actor=actor, action="recommendation.generated", entity_type="recommendation", entity_id=rec.id,
                     details={"rule_id": rec.rule_id, "rule_version": rec.rule_version, "priority": rec.priority.value,
                              "rank_score": rec.rank_score, "scope": rec.scope.value})
    for old, new in result.superseded:
        record_audit(db, actor=actor, action="recommendation.superseded", entity_type="recommendation", entity_id=old.id,
                     details={"rule_id": old.rule_id, "superseded_by_id": new.id if new else None,
                              "reason": "evidence_changed" if new else "evidence_no_longer_current"})
    previously_critical = {new.id for old, new in result.superseded if new and old.priority == RecommendationPriority.CRITICAL}
    for rec in result.created:
        if rec.priority != RecommendationPriority.CRITICAL or rec.employee is None or rec.id in previously_critical:
            continue
        recipients = set(department_manager_user_ids(db, rec.employee.department_id)) - {actor.id, rec.employee.user_id}
        for user_id in sorted(recipients):
            notify(db, user_id=user_id, category="recommendation", title="Critical recommendation to review",
                   message=f"Recommendation #{rec.id} (CRITICAL priority) needs review.", entity_type="recommendation",
                   entity_id=rec.id)


def _ranked(db: DbSession, result: engine.EngineResult) -> list[RankedCandidate]:
    if not result.ranked:
        return []
    created = {r.dedup_key: r.id for r in result.created}
    known = dict(db.execute(select(Recommendation.dedup_key, Recommendation.id).where(
        Recommendation.dedup_key.in_([c.dedup_key for c in result.ranked]))).all())
    items = []
    for c in result.ranked:
        outcome = ("created" if c.dedup_key in created else "already_recorded" if c.dedup_key in known else "being_addressed")
        items.append(RankedCandidate(rule_id=c.rule_id, subject=c.subject, priority=c.priority, rank_score=c.rank_score,
                                     outcome=outcome, recommendation_id=known.get(c.dedup_key)))
    return items


@router.get("", response_model=Page[RecommendationRead])
def list_recommendations(params: Annotated[ListParams, Query()], db: DbSession, user: Reviewer) -> Page[RecommendationRead]:
    stmt = service.visible(db, user, select(Recommendation))
    for value, column in ((params.status, Recommendation.status), (params.scope, Recommendation.scope),
                          (params.category, Recommendation.category), (params.priority, Recommendation.priority),
                          (params.rule_id, Recommendation.rule_id), (params.employee_id, Recommendation.employee_id),
                          (params.department_id, Recommendation.department_id),
                          (params.rule_version, Recommendation.rule_version)):
        if value is not None:
            stmt = stmt.where(column == value)
    if params.sort == "rank":
        priority_rank = case({p: i for i, p in enumerate(RecommendationPriority)}, value=Recommendation.priority)
        order = (Recommendation.rank_score.is_(None), Recommendation.rank_score.desc(), priority_rank.desc(),
                 Recommendation.evaluated_at.desc(), Recommendation.id.desc())
    else:
        order = (Recommendation.evaluated_at.desc(), Recommendation.id.desc())
    items, total = paginate(db, stmt.order_by(*order), params)
    return Page[RecommendationRead].build([RecommendationRead.model_validate(r) for r in items], total, params)


# --- Single recommendation -------------------------------------------------------------------------


@router.get("/{recommendation_id}", response_model=RecommendationRead)
def get_recommendation(recommendation_id: int, db: DbSession, user: Reviewer) -> RecommendationRead:
    return RecommendationRead.model_validate(_load(db, user, recommendation_id))


@router.post("/{recommendation_id}/accept", response_model=RecommendationRead)
def accept(recommendation_id: int, payload: VersionedAction, db: DbSession, actor: Reviewer) -> RecommendationRead:
    rec = _load(db, actor, recommendation_id)
    if rec.status == RecommendationStatus.ACCEPTED:
        return RecommendationRead.model_validate(rec)
    allowed, target = service.LIFECYCLE["accept"]
    if rec.status not in allowed:
        raise _error(status.HTTP_409_CONFLICT, f"A {rec.status.value} recommendation cannot be accepted")
    _check_version(rec, payload.expected_version)
    if rec.rule_version == "rules-v2" and rec.employee is not None:
        # Accepted rules-v2 guidance needs follow-up: tell the employee's department managers (never the actor or employee).
        for user_id in sorted(set(department_manager_user_ids(db, rec.employee.department_id)) - {actor.id, rec.employee.user_id}):
            notify(db, user_id=user_id, category="recommendation", title="Recommendation accepted for follow-up",
                   message=f"Recommendation #{rec.id} ({rec.priority.value} priority) was accepted and needs follow-up.",
                   entity_type="recommendation", entity_id=rec.id)
    return _decide(db, actor, rec, "accepted", allowed, {"status": target})


@router.post("/{recommendation_id}/dismiss", response_model=RecommendationRead)
def dismiss(recommendation_id: int, payload: DismissRequest, db: DbSession, actor: Reviewer) -> RecommendationRead:
    rec = _load(db, actor, recommendation_id)
    if rec.status == RecommendationStatus.DISMISSED:
        return RecommendationRead.model_validate(rec)
    allowed, target = service.LIFECYCLE["dismiss"]
    if rec.status not in allowed:
        raise _error(status.HTTP_409_CONFLICT, f"A {rec.status.value} recommendation cannot be dismissed")
    _check_version(rec, payload.expected_version)
    return _decide(db, actor, rec, "dismissed", allowed, {"status": target, "dismissal_reason": payload.reason})


@router.post("/{recommendation_id}/complete", response_model=RecommendationRead)
def complete(recommendation_id: int, payload: CompleteRequest, db: DbSession, actor: Reviewer) -> RecommendationRead:
    """ACCEPTED → COMPLETED with an outcome. Never resolves a linked risk alert or intervention."""
    rec = _load(db, actor, recommendation_id)
    if rec.status == RecommendationStatus.COMPLETED:
        return RecommendationRead.model_validate(rec)
    allowed, target = service.LIFECYCLE["complete"]
    if rec.status not in allowed:
        raise _error(status.HTTP_409_CONFLICT, "Only an ACCEPTED recommendation can be completed")
    _check_version(rec, payload.expected_version)
    return _decide(db, actor, rec, "completed", allowed, {"status": target, "completion_outcome": payload.outcome})


@router.post("/{recommendation_id}/create-intervention", response_model=RecommendationRead)
def create_intervention(recommendation_id: int, payload: CreateInterventionRequest, db: DbSession, actor: Reviewer) -> RecommendationRead:
    """Explicitly create (or link) an intervention for an ACCEPTED individual recommendation, through the
    normal intervention workflow and its permission and duplicate checks."""
    rec = _load(db, actor, recommendation_id)
    if rec.scope != RecommendationScope.INDIVIDUAL or rec.category not in INTERVENTION_CATEGORY:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only individual support recommendations can be linked to an intervention")
    if rec.status != RecommendationStatus.ACCEPTED:
        raise _error(status.HTTP_409_CONFLICT, "Accept the recommendation before creating an intervention")
    if rec.intervention_id is not None:
        if payload.intervention_id in (None, rec.intervention_id):
            return RecommendationRead.model_validate(rec)  # repeat request
        raise _error(status.HTTP_409_CONFLICT, "This recommendation is already linked to another intervention")

    if payload.intervention_id is not None:
        intervention = db.scalar(intervention_service.visible(db, actor, select(Intervention))
                                 .where(Intervention.id == payload.intervention_id))
        if intervention is None:
            raise _error(status.HTTP_404_NOT_FOUND, "Intervention not found")
        if intervention.employee_id != rec.employee_id:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The intervention is for a different employee")
        created = False
    else:
        try:
            intervention = intervention_service.create(
                db, actor, utcnow(), _today(), employee_id=rec.employee_id, category=INTERVENTION_CATEGORY[rec.category],
                title=payload.title or rec.title, description=payload.description,
                priority=payload.priority or INTERVENTION_PRIORITY[rec.priority], due_date=payload.due_date,
                assigned_to_user_id=payload.assigned_to_user_id, risk_alert_id=rec.risk_alert_id, idempotency_key=None,
                audit=record_audit, notifier=notify)
        except intervention_service.InterventionError as exc:
            db.rollback()
            raise _error(exc.status_code, exc.detail) from None
        except IntegrityError:
            db.rollback()
            raise _error(status.HTTP_409_CONFLICT, intervention_service.DUPLICATE_ACTIVE) from None
        created = True
    try:
        service.change(db, rec, (RecommendationStatus.ACCEPTED,), {"intervention_id": intervention.id})
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT) from None
    record_audit(db, actor=actor, action="recommendation.intervention_linked", entity_type="recommendation",
                 entity_id=rec.id, details={"rule_id": rec.rule_id, "intervention_id": intervention.id, "created": created})
    db.commit()
    return RecommendationRead.model_validate(_load(db, actor, rec.id))


@router.get("/{recommendation_id}/history", response_model=RecommendationHistory)
def recommendation_history(recommendation_id: int, db: DbSession, user: Reviewer) -> RecommendationHistory:
    """Decisions and engine changes from the audit trail (sanitized; managers do not see actors outside their
    department). rules-v1 records have no per-record generation event; their evaluated_at is the generation time."""
    rec = _load(db, user, recommendation_id)
    rows = list(db.scalars(select(AuditLog).where(AuditLog.entity_type == "recommendation",
                                                  AuditLog.entity_id == str(rec.id))
                           .order_by(AuditLog.created_at, AuditLog.id).limit(200)))
    restricted = user.role not in (UserRole.ADMIN, UserRole.HR)
    scope = audit_query.AuditScope(department_id=employee_scope(db, user).department_id, viewer_user_id=user.id,
                                   restricted=restricted)
    visible_actors = audit_query.visible_actor_ids(db, scope, {r.actor_user_id for r in rows if r.actor_user_id})
    events = [HistoryEvent(at=i["created_at"], action=i["action"], actor_user_id=i["actor_user_id"],
                           actor_hidden=i["actor_hidden"], details=i["details"])
              for i in audit_query.present(rows, visible_actors)]
    return RecommendationHistory(recommendation_id=rec.id, rule_id=rec.rule_id, rule_version=rec.rule_version,
                                 evaluated_at=rec.evaluated_at, status=rec.status, superseded_by_id=rec.superseded_by_id,
                                 events=events)


@router.post("/{recommendation_id}/link-task", response_model=RecommendationRead)
def link_task(recommendation_id: int, payload: LinkTaskRequest, db: DbSession, actor: Reviewer) -> RecommendationRead:
    """Explicitly link an ACCEPTED recommendation to an existing task you manage (tasks are never created here)."""
    rec = _load(db, actor, recommendation_id)
    if rec.status != RecommendationStatus.ACCEPTED:
        raise _error(status.HTTP_409_CONFLICT, "Accept the recommendation before linking a task")
    viewer = task_service.viewer(db, actor)
    task = db.scalar(task_service.visible(viewer, select(Task)).where(Task.id == payload.task_id))
    if task is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Task not found")
    if not task_service.can_manage(viewer, task):
        raise _error(status.HTTP_403_FORBIDDEN, "Only the task's managers can link it")
    if rec.task_id == task.id:
        return RecommendationRead.model_validate(rec)  # repeat request
    if rec.task_id is not None:
        raise _error(status.HTTP_409_CONFLICT, "This recommendation is already linked to another task")
    if task.status == TaskStatus.CANCELLED:
        raise _error(status.HTTP_409_CONFLICT, "A CANCELLED task cannot be linked")
    department = rec.employee.department_id if rec.scope == RecommendationScope.INDIVIDUAL else rec.department_id
    if department is not None and task.department_id != department:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The task belongs to a different department")
    _check_version(rec, payload.expected_version)
    try:
        service.change(db, rec, (RecommendationStatus.ACCEPTED,), {"task_id": task.id})
    except service.ConcurrentChange:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, CONCURRENT) from None
    record_audit(db, actor=actor, action="recommendation.task_linked", entity_type="recommendation", entity_id=rec.id,
                 details={"rule_id": rec.rule_id, "rule_version": rec.rule_version, "task_id": task.id})
    db.commit()
    return RecommendationRead.model_validate(_load(db, actor, rec.id))
