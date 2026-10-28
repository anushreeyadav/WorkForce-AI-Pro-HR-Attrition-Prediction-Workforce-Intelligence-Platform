"""Recommendation evaluation over existing records only (risk alerts, stored predictions, snapshots, audit trail).

Never trains or calls a model, never creates predictions or interventions, and never changes alerts or
employee data. New AVAILABLE recommendations are inserted idempotently via their unique dedup_key.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Select, and_, false, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    AttritionModelVersion,
    AttritionPrediction,
    Department,
    Employee,
    Intervention,
    Recommendation,
    RecommendationScope,
    RecommendationStatus,
    RiskAlert,
    User,
    UserRole,
)
from app.models.intervention import ACTIVE_STATUSES as ACTIVE_INTERVENTIONS
from app.models.risk_monitoring import ACTIVE_ALERT_STATUSES
from app.services import recommendation_rules as rules
from app.services import risk_monitoring
from app.services import workforce_analytics as wa
from app.services.employee_access import employee_scope
from ml.attrition.features import FEATURE_SCHEMA_VERSION

ACTIVE = (RecommendationStatus.AVAILABLE, RecommendationStatus.ACCEPTED)
# Lifecycle (single source of truth): action -> (allowed from, to). "supersede" is system-only (rules-v2 evaluation).
LIFECYCLE = {
    "accept": ((RecommendationStatus.AVAILABLE,), RecommendationStatus.ACCEPTED),
    "dismiss": (ACTIVE, RecommendationStatus.DISMISSED),
    "complete": ((RecommendationStatus.ACCEPTED,), RecommendationStatus.COMPLETED),
    "supersede": ((RecommendationStatus.AVAILABLE,), RecommendationStatus.SUPERSEDED),
}


class ConcurrentChange(Exception):
    pass


# --- Visibility --------------------------------------------------------------------------------


def visible(db: Session, user: User, stmt: Select) -> Select:
    """ADMIN/HR: everything except recommendations about themselves. MANAGER: individual ones for their
    department (not themselves) and their department's aggregate ones. Never organization-level for managers."""
    stmt = stmt.outerjoin(Employee, Employee.id == Recommendation.employee_id)
    not_self = or_(Recommendation.employee_id.is_(None), Employee.user_id.is_(None), Employee.user_id != user.id)
    if user.role in (UserRole.ADMIN, UserRole.HR):
        return stmt.where(not_self)
    department = employee_scope(db, user).department_id
    if department is None:
        return stmt.where(false())
    return stmt.where(not_self, or_(
        and_(Recommendation.scope == RecommendationScope.INDIVIDUAL, Employee.department_id == department),
        and_(Recommendation.scope == RecommendationScope.DEPARTMENT, Recommendation.department_id == department)))


# --- Fact gathering ----------------------------------------------------------------------------


def alert_facts(db: Session, settings: Settings, user: User, now: datetime, department_id: int | None, limit: int) -> list[rules.AlertFacts]:
    stmt = risk_monitoring.visible_alerts(db, user, select(RiskAlert)).where(RiskAlert.status.in_(ACTIVE_ALERT_STATUSES))
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    alerts = list(db.scalars(stmt.order_by(RiskAlert.id).limit(limit)))
    if not alerts:
        return []
    with_intervention = set(db.scalars(select(Intervention.risk_alert_id).where(
        Intervention.risk_alert_id.in_([a.id for a in alerts]), Intervention.status.in_(ACTIVE_INTERVENTIONS))))
    predictions = {p.id: (p, m) for p, m in db.execute(
        select(AttritionPrediction, AttritionModelVersion)
        .join(AttritionModelVersion, AttritionModelVersion.id == AttritionPrediction.model_version_id)
        .where(AttritionPrediction.id.in_([a.latest_prediction_id for a in alerts])))}
    stale_after = timedelta(days=settings.risk_stale_prediction_days)

    facts = []
    for alert in alerts:
        prediction, model = predictions.get(alert.latest_prediction_id, (None, None))
        compatible = (prediction is not None and prediction.feature_schema_version == FEATURE_SCHEMA_VERSION
                      and model.feature_schema_version == FEATURE_SCHEMA_VERSION)
        explanation = (prediction.explanation or {}) if prediction else {}
        facts.append(rules.AlertFacts(
            alert_id=alert.id, employee_id=alert.employee_id, priority=alert.priority.value,
            last_observed_at=alert.last_observed_at, has_active_intervention=alert.id in with_intervention,
            prediction_id=prediction.id if prediction else None, prediction_compatible=compatible,
            prediction_fresh=prediction is not None and now - prediction.predicted_at <= stale_after,
            predicted_at=prediction.predicted_at if prediction else None,
            factors=list(explanation.get("factors", [])), missing_inputs=list(explanation.get("missing_inputs", []))))
    return facts


def aggregate_facts(db: Session, settings: Settings, user: User, now: datetime, department_id: int | None) -> rules.AggregateFacts:
    tz = settings.org_tz
    today = now.astimezone(tz).date()
    window_start = wa.stability_window_start(settings, today)
    all_obs = wa.observations(db, department_id)
    window_obs = [o for o in all_obs if window_start <= o.snapshot_date <= today]
    # The same indicator as GET /api/workforce-analytics/stability (one shared implementation).
    stability = wa.stability_report(db, settings, department_id, today, window_observations=window_obs)
    latest_snapshot_date = all_obs[-1].snapshot_date if all_obs else None

    alerts = None
    if department_id is not None:
        alerts = db.scalar(risk_monitoring.visible_alerts(db, user, select(func.count(RiskAlert.id)))
                           .where(RiskAlert.status.in_(ACTIVE_ALERT_STATUSES), Employee.department_id == department_id)) or 0
    month_points = sorted((idx, o.active) for idx, o in wa.month_end(all_obs).items())
    return rules.AggregateFacts(
        scope=RecommendationScope.ORGANIZATION if department_id is None else RecommendationScope.DEPARTMENT,
        department_id=department_id, window=f"{today.year:04d}-{today.month:02d}",
        latest_snapshot_at=(datetime.combine(latest_snapshot_date, datetime.min.time(), tzinfo=tz) if latest_snapshot_date else None),
        latest_snapshot_age_days=(today - latest_snapshot_date).days if latest_snapshot_date else None,
        inactive_without_recorded_departure=wa.inactive_without_recorded_departure(db, department_id),
        annualized_turnover=stability["metrics"]["annualized_turnover"],
        stability_status=stability["status"], stability_score=stability["score"],
        month_end_active=month_points, active_alerts=alerts)


def thresholds(settings: Settings) -> rules.Thresholds:
    return rules.Thresholds(turnover=settings.recommendation_turnover_review_threshold,
                            stability_below=settings.recommendation_stability_review_below,
                            alert_cluster_min=settings.recommendation_alert_cluster_min,
                            snapshot_max_age_days=settings.workforce_snapshot_max_age_days)


# --- Evaluation ----------------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    created: list[Recommendation] = field(default_factory=list)
    already_recorded: int = 0
    outcomes: list[rules.Outcome] = field(default_factory=list)
    alerts_examined: int = 0
    subjects_examined: int = 0


def existing_keys(db: Session, keys: list[str]) -> set[str]:
    return set(db.scalars(select(Recommendation.dedup_key).where(Recommendation.dedup_key.in_(keys)))) if keys else set()


def evaluate(db: Session, settings: Settings, user: User, now: datetime, department_id: int | None,
             include_individual: bool, limit: int) -> EvaluationResult:
    """department_id None = organization-level aggregate rules plus every department (ADMIN/HR only)."""
    result = EvaluationResult()
    candidates: list[rules.Candidate] = []
    limits = thresholds(settings)

    if department_id is None:
        aggregate_subjects = [None] + list(db.scalars(select(Department.id).order_by(Department.id)))
    else:
        aggregate_subjects = [department_id]
    for subject in aggregate_subjects:
        outcome = rules.aggregate_rules(aggregate_facts(db, settings, user, now, subject), limits)
        candidates += outcome.candidates
        result.outcomes += outcome.outcomes
        result.subjects_examined += 1

    if include_individual:
        facts = alert_facts(db, settings, user, now, department_id, limit)
        result.alerts_examined = len(facts)
        for item in facts:
            outcome = rules.individual_rules(item)
            candidates += outcome.candidates
            result.outcomes += outcome.outcomes

    seen = existing_keys(db, [c.dedup_key for c in candidates])
    for candidate in candidates:
        if candidate.dedup_key in seen:
            result.already_recorded += 1
            continue
        seen.add(candidate.dedup_key)
        row = Recommendation(
            rule_id=candidate.rule_id, rule_version=rules.RULE_VERSION, scope=candidate.scope, category=candidate.category,
            priority=candidate.priority, status=RecommendationStatus.AVAILABLE, employee_id=candidate.employee_id,
            department_id=candidate.department_id, risk_alert_id=candidate.risk_alert_id, title=candidate.title,
            suggested_action=candidate.suggested_action, explanation=candidate.explanation, evidence=candidate.evidence,
            evidence_window=candidate.evidence_window, source_data_as_of=candidate.source_data_as_of, evaluated_at=now,
            dedup_key=candidate.dedup_key, version=1)
        db.add(row)
        result.created.append(row)
    db.flush()  # a concurrent evaluation inserting the same dedup_key raises IntegrityError here
    return result


# --- Lifecycle -----------------------------------------------------------------------------------


def change(db: Session, rec: Recommendation, allowed_from: tuple[RecommendationStatus, ...], values: dict) -> None:
    """Conditional update on (id, version, status); raises ConcurrentChange if anything moved."""
    changed = db.execute(
        update(Recommendation)
        .where(Recommendation.id == rec.id, Recommendation.version == rec.version, Recommendation.status.in_(allowed_from))
        .values(**values, version=rec.version + 1).execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise ConcurrentChange()
