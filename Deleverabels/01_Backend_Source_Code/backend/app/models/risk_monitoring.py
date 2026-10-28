"""Risk-monitoring events and alerts, derived from persisted attrition predictions.

Events are an append-only history of detected conditions (deduplicated by a stable key). Alerts are the
HR review workflow; several events can attach to one active alert. Neither stores feature values, and
nothing here changes employee, attendance, leave or payroll data.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime, utcnow
from app.models.enums import AlertPriority, AlertStatus, EscalationStage, RiskCategory, RiskEventType

if TYPE_CHECKING:
    from app.models.employee import Employee

ACTIVE_ALERT_STATUSES = (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)


class RiskAlert(TimestampMixin, Base):
    """At most one active (OPEN/ACKNOWLEDGED) alert per employee, enforced by the monitoring service."""

    __tablename__ = "risk_alerts"
    __table_args__ = (
        CheckConstraint("latest_score >= 0 AND latest_score <= 100", name="latest_score_range"),
        CheckConstraint("event_count >= 1", name="event_count_positive"),
        Index("ix_risk_alerts_employee_status", "employee_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    trigger_type: Mapped[RiskEventType] = mapped_column(Enum(RiskEventType, name="risk_event_type"), nullable=False)
    priority: Mapped[AlertPriority] = mapped_column(Enum(AlertPriority, name="alert_priority"), nullable=False, index=True)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus, name="alert_status"), nullable=False, index=True)
    # Snapshot of the most recent prediction that supported this alert (score/category only).
    latest_prediction_id: Mapped[int] = mapped_column(ForeignKey("attrition_predictions.id", ondelete="RESTRICT"), nullable=False)
    latest_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    latest_category: Mapped[RiskCategory] = mapped_column(Enum(RiskCategory, name="risk_category"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_detected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    assigned_to_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    employee: Mapped["Employee"] = relationship()


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        UniqueConstraint("dedup_key"),
        CheckConstraint("current_score >= 0 AND current_score <= 100", name="current_score_range"),
        CheckConstraint("previous_score IS NULL OR (previous_score >= 0 AND previous_score <= 100)",
                        name="previous_score_range"),
        Index("ix_risk_events_employee_detected", "employee_id", "detected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[RiskEventType] = mapped_column(Enum(RiskEventType, name="risk_event_type"), nullable=False, index=True)
    # Stable key built from prediction ids, e.g. "THRESHOLD_CROSSED:<prediction>", so reruns never duplicate.
    dedup_key: Mapped[str] = mapped_column(String(100), nullable=False)
    current_prediction_id: Mapped[int] = mapped_column(ForeignKey("attrition_predictions.id", ondelete="RESTRICT"), nullable=False)
    previous_prediction_id: Mapped[int | None] = mapped_column(ForeignKey("attrition_predictions.id", ondelete="RESTRICT"), nullable=True)
    current_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    previous_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    current_category: Mapped[RiskCategory] = mapped_column(Enum(RiskCategory, name="risk_category"), nullable=False)
    previous_category: Mapped[RiskCategory | None] = mapped_column(Enum(RiskCategory, name="risk_category"), nullable=True)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("risk_alerts.id", ondelete="RESTRICT"), nullable=True, index=True)

    employee: Mapped["Employee"] = relationship()


class RiskAlertEscalation(Base):
    """Append-only record that an OPEN alert reached a review-deadline stage and the reviewer group was notified.

    The single source of truth for escalation history (the activity feed reads it; audit rows only record who
    ran the evaluation). One row per (alert, stage): repeated evaluations never duplicate the row or its
    notifications. A stage is only recorded when at least one eligible recipient was notified. The alert
    itself is never changed by escalation.
    """

    __tablename__ = "risk_alert_escalations"
    __table_args__ = (
        UniqueConstraint("alert_id", "stage"),
        CheckConstraint("recipient_count >= 1", name="recipient_count_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("risk_alerts.id", ondelete="RESTRICT"), nullable=False)
    stage: Mapped[EscalationStage] = mapped_column(Enum(EscalationStage, name="escalation_stage"), nullable=False)
    # Alert priority and the computed deadline at the time of escalation (the alert may change later).
    alert_priority: Mapped[AlertPriority] = mapped_column(Enum(AlertPriority, name="alert_priority"), nullable=False)
    review_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False)
    triggered_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, index=True)

# Activity-feed query indexes (see README, "Feed query indexes"): each serves a range/keyset filter on this
# timestamp with ORDER BY (timestamp, id) LIMIT n. InnoDB secondary indexes include the primary key.
Index("ix_risk_alerts_created_at", RiskAlert.created_at)
Index("ix_risk_alerts_acknowledged_at", RiskAlert.acknowledged_at)
Index("ix_risk_alerts_resolved_at", RiskAlert.resolved_at)
Index("ix_risk_events_detected_at", RiskEvent.detected_at)
