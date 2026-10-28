"""Risk-alert review deadlines (SLA) and bounded, idempotent escalation. No model is called; nothing is scheduled.

Rules (documented in README):
- Review window: ALERT_REVIEW_SLA_HOURS_<PRIORITY> for the alert's CURRENT priority, measured from
  first_detected_at (when the alert was raised; never overwritten). review_due_at = first_detected_at + window.
  If monitoring later raises the priority, the shorter window applies from the same start.
- "Reviewed" means acknowledged (or closed). State, evaluated at `now` (timezone-aware UTC):
  - CLOSED: RESOLVED or DISMISSED.
  - ACKNOWLEDGED: review has started; not escalated (acknowledged alerts are owned by a reviewer).
  - OVERDUE: OPEN and now >= review_due_at (the deadline instant itself is overdue).
  - APPROACHING: OPEN and now >= approaching_at (ALERT_REVIEW_APPROACHING_PERCENT of the window elapsed).
  - ON_TRACK: otherwise.
  For ACKNOWLEDGED/CLOSED, `met_deadline` says whether the first review action came by review_due_at.
- Escalation (explicit POST only): OPEN alerts that are APPROACHING or OVERDUE get one RiskAlertEscalation row
  per (alert, stage) and one notification per eligible recipient, in the same transaction. Recipients are
  active users with ALERT_ESCALATION_RECIPIENT_ROLES (ADMIN/HR), never the monitored employee. With no
  eligible recipient nothing is recorded, so a later evaluation can still escalate. Alert status, priority and
  assignee are never changed; no intervention or recommendation is created.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AlertPriority, AlertStatus, Employee, EscalationStage, RiskAlert, RiskAlertEscalation, User, UserRole
from app.models.risk_monitoring import ACTIVE_ALERT_STATUSES
from app.services.notifications import notify
from app.services.risk_monitoring import visible_alerts

ON_TRACK, APPROACHING, OVERDUE, ACKNOWLEDGED, CLOSED = "ON_TRACK", "APPROACHING", "OVERDUE", "ACKNOWLEDGED", "CLOSED"
SLA_STATES = (ON_TRACK, APPROACHING, OVERDUE, ACKNOWLEDGED, CLOSED)
CATEGORY = {EscalationStage.APPROACHING: "risk_alert_due_soon", EscalationStage.OVERDUE: "risk_alert_overdue"}


@dataclass(frozen=True)
class SlaPolicy:
    hours: dict[AlertPriority, int]
    approaching_percent: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "SlaPolicy":
        return cls(hours={AlertPriority.HIGH: settings.alert_review_sla_hours_high,
                          AlertPriority.MEDIUM: settings.alert_review_sla_hours_medium,
                          AlertPriority.LOW: settings.alert_review_sla_hours_low},
                   approaching_percent=settings.alert_review_approaching_percent)


@dataclass(frozen=True)
class SlaStatus:
    state: str
    review_window_hours: int
    review_due_at: datetime
    approaching_at: datetime
    met_deadline: bool | None


def sla_status(priority: AlertPriority, status: AlertStatus, first_detected_at: datetime, acknowledged_at: datetime | None,
               resolved_at: datetime | None, now: datetime, policy: SlaPolicy) -> SlaStatus:
    """Pure calculation; never changes the alert."""
    if now.tzinfo is None or first_detected_at.tzinfo is None:
        raise ValueError("SLA calculations require timezone-aware datetimes")
    hours = policy.hours[priority]
    due = first_detected_at + timedelta(hours=hours)
    approaching_at = first_detected_at + timedelta(seconds=hours * 3600 * policy.approaching_percent // 100)
    if status in (AlertStatus.RESOLVED, AlertStatus.DISMISSED):
        reviewed = acknowledged_at or resolved_at
        return SlaStatus(CLOSED, hours, due, approaching_at, None if reviewed is None else reviewed <= due)
    if status == AlertStatus.ACKNOWLEDGED:
        return SlaStatus(ACKNOWLEDGED, hours, due, approaching_at, None if acknowledged_at is None else acknowledged_at <= due)
    state = OVERDUE if now >= due else APPROACHING if now >= approaching_at else ON_TRACK
    return SlaStatus(state, hours, due, approaching_at, None)


def status_of(alert: RiskAlert, now: datetime, policy: SlaPolicy) -> SlaStatus:
    return sla_status(alert.priority, alert.status, alert.first_detected_at, alert.acknowledged_at, alert.resolved_at,
                      now, policy)


def recipients(db: Session, settings: Settings) -> list[int]:
    roles = [UserRole(r) for r in settings.alert_escalation_recipient_roles]
    return list(db.scalars(select(User.id).where(User.role.in_(roles), User.is_active.is_(True)).order_by(User.id)))


def recorded_stages(db: Session, alert_ids: list[int]) -> set[tuple[int, EscalationStage]]:
    """Pre-check; the unique (alert_id, stage) constraint is the final guard against a concurrent evaluation."""
    return {(a, s) for a, s in db.execute(select(RiskAlertEscalation.alert_id, RiskAlertEscalation.stage)
                                          .where(RiskAlertEscalation.alert_id.in_(alert_ids))).all()}


@dataclass
class EscalationOutcome:
    alert_id: int
    stage: EscalationStage
    priority: AlertPriority
    review_due_at: datetime
    recipient_count: int
    escalation_id: int


@dataclass
class EvaluationResult:
    examined: int = 0
    by_state: dict[str, int] = field(default_factory=lambda: {s: 0 for s in (ON_TRACK, APPROACHING, OVERDUE, ACKNOWLEDGED)})
    escalated: list[EscalationOutcome] = field(default_factory=list)
    already_escalated: int = 0
    approaching_not_notified: int = 0
    no_eligible_recipient: list[int] = field(default_factory=list)
    notifications_created: int = 0
    next_after_alert_id: int | None = None


def evaluate(db: Session, settings: Settings, actor: User, now: datetime, limit: int, after_alert_id: int,
             department_id: int | None) -> EvaluationResult:
    """Examine up to `limit` active alerts visible to `actor` (by id, after the cursor). A fixed number of
    queries plus one INSERT per new escalation and notification."""
    policy = SlaPolicy.from_settings(settings)
    result = EvaluationResult()
    stmt = (visible_alerts(db, actor, select(RiskAlert, Employee.user_id))
            .where(RiskAlert.status.in_(ACTIVE_ALERT_STATUSES), RiskAlert.id > after_alert_id))
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    rows = db.execute(stmt.order_by(RiskAlert.id).limit(limit + 1)).all()
    if len(rows) > limit:
        rows = rows[:limit]
        result.next_after_alert_id = rows[-1][0].id
    if not rows:
        return result

    recorded = recorded_stages(db, [a.id for a, _ in rows])
    reviewers = recipients(db, settings)

    for alert, employee_user_id in rows:
        result.examined += 1
        sla = status_of(alert, now, policy)
        result.by_state[sla.state] += 1
        if sla.state not in (APPROACHING, OVERDUE):
            continue
        stage = EscalationStage(sla.state)
        if (alert.id, stage) in recorded or (alert.id, EscalationStage.OVERDUE) in recorded:
            result.already_escalated += 1
            continue
        if stage == EscalationStage.APPROACHING and not settings.alert_escalation_notify_approaching:
            result.approaching_not_notified += 1
            continue
        targets = [u for u in reviewers if u != employee_user_id]  # never the monitored employee
        if not targets:
            result.no_eligible_recipient.append(alert.id)
            continue
        escalation = RiskAlertEscalation(alert_id=alert.id, stage=stage, alert_priority=alert.priority,
                                         review_due_at=sla.review_due_at, recipient_count=len(targets),
                                         triggered_by_user_id=actor.id, created_at=now)
        db.add(escalation)
        db.flush()
        title = "Risk alert review overdue" if stage == EscalationStage.OVERDUE else "Risk alert review due soon"
        message = (f"Risk alert #{alert.id} ({alert.priority.value} priority) has not been acknowledged and is past "
                   "its review deadline." if stage == EscalationStage.OVERDUE else
                   f"Risk alert #{alert.id} ({alert.priority.value} priority) is approaching its review deadline.")
        for user_id in targets:
            notify(db, user_id=user_id, category=CATEGORY[stage], title=title, message=message,
                   entity_type="risk_alert", entity_id=alert.id)
        result.notifications_created += len(targets)
        result.escalated.append(EscalationOutcome(alert.id, stage, alert.priority, sla.review_due_at, len(targets),
                                                  escalation.id))
    return result
