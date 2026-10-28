"""Versioned recommendation engine. Strategies share one pipeline contract; rules stay pure and separate from
persistence, serialization and notification delivery.

Pipeline (rules-v2):
  1. collect authorized signals (each source query bounded and scoped to the caller, never about the caller)
  2. validate availability/freshness (stale or incompatible predictions become data-refresh suggestions)
  3-6. evaluate the pure rules (recommendation_rules_v2): candidates with priority, rank_score, reasons, evidence
  7. deduplicate and persist:
       - same rule + subject + window + fingerprint already recorded (any status) -> nothing (no resurrection)
       - an ACCEPTED recommendation for the same rule + subject + window -> being addressed, nothing new
       - an AVAILABLE one with a different fingerprint -> new record; the old one becomes SUPERSEDED (superseded_by_id)
       - AVAILABLE rules-v2 records in the evaluated scope whose rule no longer fires -> SUPERSEDED (no successor),
         except for rule families whose source query hit the limit in this run
  8. return candidates in rank order
rules-v1 is the unchanged compatibility baseline (app/services/recommendations.evaluate). Nothing here trains or
calls a model, contacts external services, or creates interventions or tasks.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    AttritionModelVersion,
    AttritionPrediction,
    Department,
    Employee,
    Goal,
    GoalStatus,
    Intervention,
    PerformanceReview,
    Recommendation,
    RecommendationScope,
    RecommendationStatus,
    ReviewCycle,
    ReviewStatus,
    RiskAlert,
    Task,
    TaskPriority,
    User,
)
from app.models.intervention import ACTIVE_STATUSES as ACTIVE_INTERVENTIONS
from app.models.performance import ACTIVE_CYCLE_STATUSES, ACTIVE_GOAL_STATUSES
from app.models.risk_monitoring import ACTIVE_ALERT_STATUSES
from app.models.task import ACTIVE_TASK_STATUSES
from app.services import alert_sla
from app.services import interventions as intervention_service
from app.services import performance as performance_service
from app.services import recommendation_rules as v1_rules
from app.services import recommendation_rules_v2 as v2
from app.services import recommendations as v1_service
from app.services import risk_monitoring
from app.services import tasks as task_service
from ml.attrition.features import FEATURE_SCHEMA_VERSION

S = RecommendationScope
INDIVIDUAL_FAMILIES = {"alerts": ("V2_ALERT_SUPPORT_REVIEW", "V2_PREDICTION_REFRESH"),
                       "interventions": ("V2_INTERVENTION_FOLLOW_UP",), "goals": ("V2_GOAL_CHECK_IN",)}


@dataclass
class EngineResult:
    rule_version: str
    created: list[Recommendation] = field(default_factory=list)
    already_recorded: int = 0
    being_addressed: int = 0
    superseded: list[tuple[Recommendation, Recommendation | None]] = field(default_factory=list)  # (old, new or None)
    outcomes: list = field(default_factory=list)
    alerts_examined: int = 0
    subjects_examined: int = 0
    truncated_sources: list[str] = field(default_factory=list)
    ranked: list[v2.Candidate] = field(default_factory=list)


# --- Strategy registry ------------------------------------------------------------------------------------


def run_v1(db: Session, settings: Settings, user: User, now: datetime, department_id: int | None,
           include_individual: bool, limit: int) -> EngineResult:
    """Compatibility baseline: the unchanged rules-v1 evaluation."""
    result = v1_service.evaluate(db, settings, user, now, department_id, include_individual, limit)
    return EngineResult(v1_rules.RULE_VERSION, created=result.created, already_recorded=result.already_recorded,
                        outcomes=result.outcomes, alerts_examined=result.alerts_examined,
                        subjects_examined=result.subjects_examined)


def run_v2(db: Session, settings: Settings, user: User, now: datetime, department_id: int | None,
           include_individual: bool, limit: int) -> EngineResult:
    result = EngineResult(v2.RULE_VERSION)
    rules = v2.RuleResult()
    tz = settings.org_tz
    today = now.astimezone(tz).date()
    limits = v2.Limits(stability_below=settings.recommendation_stability_review_below)
    viewer = task_service.viewer(db, user)
    complete_families = set()

    if include_individual:
        alerts, truncated = collect_alerts(db, settings, user, now, department_id, limit)
        result.alerts_examined = len(alerts)
        for signal in alerts:
            v2.alert_rules(signal, now, rules)
        interventions, truncated_i = collect_interventions(db, user, today, department_id, limit)
        for signal in interventions:
            v2.intervention_rules(signal, now, today, limits, rules)
        goals, truncated_g = collect_goals(db, viewer, today, department_id, limit)
        for signal in goals:
            v2.goal_rules(signal, now, rules)
        for family, cut in (("alerts", truncated), ("interventions", truncated_i), ("goals", truncated_g)):
            if cut:
                result.truncated_sources.append(family)
            else:
                complete_families |= set(INDIVIDUAL_FAMILIES[family])

    subjects = [None, *db.scalars(select(Department.id).order_by(Department.id))] if department_id is None else [department_id]
    for subject in subjects:
        v2.aggregate_rules(collect_aggregate(db, settings, user, viewer, now, today, subject), now, limits, rules)
        result.subjects_examined += 1
    complete_families |= {"V2_STABILITY_REVIEW", "V2_TASK_FOLLOW_UP", "V2_REVIEW_FOLLOW_UP"}

    result.outcomes = rules.outcomes
    result.ranked = rules.ranked()
    persist_v2(db, user, now, department_id, result, complete_families)
    return result


STRATEGIES = {v1_rules.RULE_VERSION: run_v1, v2.RULE_VERSION: run_v2}


# --- Signal collection (bounded, scoped) -------------------------------------------------------------


def collect_alerts(db: Session, settings: Settings, user: User, now: datetime, department_id: int | None,
                   limit: int) -> tuple[list[v2.AlertSignal], bool]:
    stmt = (risk_monitoring.visible_alerts(db, user, select(RiskAlert, Employee.department_id))
            .where(RiskAlert.status.in_(ACTIVE_ALERT_STATUSES)))
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    rows = db.execute(stmt.order_by(RiskAlert.id).limit(limit + 1)).all()
    truncated = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return [], truncated
    alert_ids = [a.id for a, _ in rows]
    with_intervention = set(db.scalars(select(Intervention.risk_alert_id).where(
        Intervention.risk_alert_id.in_(alert_ids), Intervention.status.in_(ACTIVE_INTERVENTIONS))))
    predictions = {p.id: (p, m) for p, m in db.execute(
        select(AttritionPrediction, AttritionModelVersion)
        .join(AttritionModelVersion, AttritionModelVersion.id == AttritionPrediction.model_version_id)
        .where(AttritionPrediction.id.in_([a.latest_prediction_id for a, _ in rows])))}
    policy = alert_sla.SlaPolicy.from_settings(settings)
    stale_after = timedelta(days=settings.risk_stale_prediction_days)
    signals = []
    for alert, department in rows:
        prediction, model = predictions.get(alert.latest_prediction_id, (None, None))
        if prediction is None:
            problem = "missing"
        elif prediction.feature_schema_version != FEATURE_SCHEMA_VERSION or model.feature_schema_version != FEATURE_SCHEMA_VERSION:
            problem = "incompatible"
        elif now - prediction.predicted_at > stale_after:
            problem = "stale"
        else:
            problem = None
        signals.append(v2.AlertSignal(
            alert_id=alert.id, employee_id=alert.employee_id, department_id=department,
            alert_priority=alert.priority.value, sla_state=alert_sla.status_of(alert, now, policy).state,
            has_active_intervention=alert.id in with_intervention, prediction_problem=problem,
            last_observed_at=alert.last_observed_at))
    return signals, truncated


def collect_interventions(db: Session, user: User, today: date, department_id: int | None,
                          limit: int) -> tuple[list[v2.InterventionSignal], bool]:
    stmt = (intervention_service.visible(db, user, select(Intervention, Employee.department_id))
            .where(Intervention.status.in_(ACTIVE_INTERVENTIONS), Intervention.due_date < today))
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    rows = db.execute(stmt.order_by(Intervention.id).limit(limit + 1)).all()
    return [v2.InterventionSignal(i.id, i.employee_id, department, i.priority.value, i.due_date, i.updated_at)
            for i, department in rows[:limit]], len(rows) > limit


def collect_goals(db: Session, viewer: task_service.Viewer, today: date, department_id: int | None,
                  limit: int) -> tuple[list[v2.GoalSignal], bool]:
    stmt = (performance_service.visible_goals(viewer, select(Goal.id, Goal.employee_id, Employee.department_id,
                                                             Goal.status, Goal.target_date, Goal.updated_at))
            .where(Goal.status.in_(ACTIVE_GOAL_STATUSES),
                   (Goal.status == GoalStatus.AT_RISK) | (Goal.target_date < today)))
    if viewer.employee_id is not None:
        stmt = stmt.where(Goal.employee_id != viewer.employee_id)  # never about the caller
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    rows = db.execute(stmt.order_by(Goal.employee_id, Goal.id).limit(limit + 1)).all()
    by_employee: dict[int, dict] = {}
    for goal_id, employee_id, department, goal_status, target, updated in rows[:limit]:
        entry = by_employee.setdefault(employee_id, {"department": department, "risk": [], "late": [], "updated": updated})
        (entry["risk"] if goal_status == GoalStatus.AT_RISK else entry["late"]).append(goal_id)
        entry["updated"] = max(entry["updated"], updated)
    return [v2.GoalSignal(employee_id, e["department"], tuple(e["risk"]), tuple(e["late"]), e["updated"])
            for employee_id, e in sorted(by_employee.items())], len(rows) > limit


def collect_aggregate(db: Session, settings: Settings, user: User, viewer: task_service.Viewer, now: datetime,
                      today: date, department_id: int | None) -> v2.AggregateSignal:
    facts = v1_service.aggregate_facts(db, settings, user, now, department_id)  # same stability calculation as v1
    tasks = reviews = critical = oldest_task = oldest_review = None
    if department_id is not None:
        task_rows = db.execute(task_service.visible(viewer, select(Task.priority, Task.due_date)).where(
            Task.department_id == department_id, Task.status.in_(ACTIVE_TASK_STATUSES),
            Task.priority.in_((TaskPriority.HIGH, TaskPriority.CRITICAL)), Task.due_date < today)).all()
        tasks = len(task_rows)
        critical = sum(1 for priority, _ in task_rows if priority == TaskPriority.CRITICAL)
        oldest_task = max(((today - due).days for _, due in task_rows), default=None)
        review_stmt = (performance_service.visible_reviews(
            viewer, select(ReviewCycle.manager_review_deadline).select_from(PerformanceReview))
                       .join(ReviewCycle, ReviewCycle.id == PerformanceReview.cycle_id)
                       .where(Employee.department_id == department_id, ReviewCycle.status.in_(ACTIVE_CYCLE_STATUSES),
                              PerformanceReview.status.in_((ReviewStatus.ASSIGNED, ReviewStatus.SELF_SUBMITTED)),
                              ReviewCycle.manager_review_deadline < today))
        if viewer.employee_id is not None:
            review_stmt = review_stmt.where(PerformanceReview.employee_id != viewer.employee_id)
        deadlines = db.scalars(review_stmt).all()
        reviews = len(deadlines)
        oldest_review = max(((today - d).days for d in deadlines), default=None)
    return v2.AggregateSignal(
        scope=facts.scope, department_id=department_id, window=facts.window, stability_status=facts.stability_status,
        stability_score=facts.stability_score, month_end_active=tuple(facts.month_end_active),
        latest_snapshot_at=facts.latest_snapshot_at, overdue_high_tasks=tasks, overdue_critical_tasks=critical,
        oldest_task_overdue_days=oldest_task, overdue_reviews=reviews, oldest_review_overdue_days=oldest_review)


# --- Persistence --------------------------------------------------------------------------------------------


def _lineage(rec: Recommendation) -> tuple[str, str, str]:
    subject = (f"employee:{rec.employee_id}" if rec.scope == S.INDIVIDUAL else
               f"department:{rec.department_id}" if rec.scope == S.DEPARTMENT else "organization")
    return rec.rule_id, subject, rec.evidence_window


def persist_v2(db: Session, user: User, now: datetime, department_id: int | None, result: EngineResult,
               complete_families: set[str]) -> None:
    candidates = result.ranked
    existing_keys = v1_service.existing_keys(db, [c.dedup_key for c in candidates])
    # Active rules-v2 records the caller can see, in the evaluated scope.
    scope_stmt = v1_service.visible(db, user, select(Recommendation)).where(
        Recommendation.rule_version == v2.RULE_VERSION,
        Recommendation.status.in_((RecommendationStatus.AVAILABLE, RecommendationStatus.ACCEPTED)))
    if department_id is not None:
        scope_stmt = scope_stmt.where(((Recommendation.scope == S.INDIVIDUAL) & (Employee.department_id == department_id))
                                      | ((Recommendation.scope == S.DEPARTMENT) & (Recommendation.department_id == department_id)))
    active = list(db.scalars(scope_stmt))
    by_lineage = {_lineage(r): r for r in active}
    produced = set()

    for candidate in candidates:
        produced.add(candidate.lineage)
        if candidate.dedup_key in existing_keys:
            result.already_recorded += 1
            continue
        current = by_lineage.get(candidate.lineage)
        if current is not None and current.status == RecommendationStatus.ACCEPTED:
            result.being_addressed += 1
            continue
        row = Recommendation(
            rule_id=candidate.rule_id, rule_version=v2.RULE_VERSION, scope=candidate.scope, category=candidate.category,
            priority=candidate.priority, status=RecommendationStatus.AVAILABLE, employee_id=candidate.employee_id,
            department_id=candidate.department_id if candidate.scope != S.INDIVIDUAL else None,
            risk_alert_id=candidate.risk_alert_id, title=candidate.title, suggested_action=candidate.suggested_action,
            explanation=candidate.explanation, evidence=candidate.evidence, evidence_window=candidate.evidence_window,
            source_data_as_of=candidate.source_data_as_of, evaluated_at=now, dedup_key=candidate.dedup_key,
            rank_score=candidate.rank_score, priority_reasons=list(candidate.priority_reasons),
            evidence_fingerprint=candidate.fingerprint, version=1)
        db.add(row)
        db.flush()  # a concurrent evaluation inserting the same dedup_key raises IntegrityError here
        existing_keys.add(candidate.dedup_key)
        result.created.append(row)
        if current is not None:  # AVAILABLE with different evidence: superseded by the new record
            v1_service.change(db, current, (RecommendationStatus.AVAILABLE,),
                              {"status": RecommendationStatus.SUPERSEDED, "superseded_by_id": row.id})
            result.superseded.append((current, row))

    for rec in active:
        if (rec.status == RecommendationStatus.AVAILABLE and _lineage(rec) not in produced
                and rec.rule_id in complete_families):
            v1_service.change(db, rec, (RecommendationStatus.AVAILABLE,), {"status": RecommendationStatus.SUPERSEDED})
            result.superseded.append((rec, None))
    db.flush()
