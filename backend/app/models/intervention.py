"""HR interventions: retention and workforce-support actions, optionally linked to a risk alert.

Internal workflow records only. They do not change employee, attendance, leave, payroll or snapshot data,
never change the linked risk alert, and store no prediction features. Every change bumps `version`
(optimistic concurrency) and appends an InterventionEvent with that version.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, Date, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime, utcnow
from app.models.enums import (
    InterventionCategory,
    InterventionEventType,
    InterventionPriority,
    InterventionStatus,
)

if TYPE_CHECKING:
    from app.models.employee import Employee

ACTIVE_STATUSES = (InterventionStatus.OPEN, InterventionStatus.IN_PROGRESS)


class Intervention(TimestampMixin, Base):
    __tablename__ = "interventions"
    __table_args__ = (
        # "<risk_alert_id>:<category>" while active and alert-linked, otherwise NULL: at most one active
        # intervention per alert and category (NULLs are not compared by unique constraints).
        UniqueConstraint("active_alert_key"),
        UniqueConstraint("idempotency_key"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("status <> 'COMPLETED' OR (completed_at IS NOT NULL AND outcome IS NOT NULL)",
                        name="completed_has_outcome"),
        CheckConstraint("status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL)",
                        name="cancelled_has_reason"),
        CheckConstraint("status IN ('OPEN', 'IN_PROGRESS') OR active_alert_key IS NULL", name="inactive_has_no_key"),
        Index("ix_interventions_employee_status", "employee_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    risk_alert_id: Mapped[int | None] = mapped_column(ForeignKey("risk_alerts.id", ondelete="RESTRICT"), nullable=True, index=True)
    category: Mapped[InterventionCategory] = mapped_column(Enum(InterventionCategory, name="intervention_category"), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    priority: Mapped[InterventionPriority] = mapped_column(Enum(InterventionPriority, name="intervention_priority"),
                                                           nullable=False, index=True)
    status: Mapped[InterventionStatus] = mapped_column(Enum(InterventionStatus, name="intervention_status"),
                                                       nullable=False, index=True)
    assigned_to_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_alert_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    employee: Mapped["Employee"] = relationship()


class InterventionEvent(Base):
    """Append-only workflow history (status changes, assignments, updates, notes)."""

    __tablename__ = "intervention_events"
    __table_args__ = (
        UniqueConstraint("intervention_id", "version", name="uq_intervention_events_intervention_id_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intervention_id: Mapped[int] = mapped_column(ForeignKey("interventions.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[InterventionEventType] = mapped_column(Enum(InterventionEventType, name="intervention_event_type"), nullable=False)
    from_status: Mapped[InterventionStatus | None] = mapped_column(Enum(InterventionStatus, name="intervention_status"), nullable=True)
    to_status: Mapped[InterventionStatus | None] = mapped_column(Enum(InterventionStatus, name="intervention_status"), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Follow-up note text (NOTE_ADDED only); never copied into audit rows or notifications.
    note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Field names changed, assignee ids, etc. No free text.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

# Activity-feed query indexes (see README, "Feed query indexes"): each serves a range/keyset filter on this
# timestamp with ORDER BY (timestamp, id) LIMIT n. InnoDB secondary indexes include the primary key.
Index("ix_intervention_events_created_at", InterventionEvent.created_at)
