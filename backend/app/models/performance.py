"""Performance management: review cycles, employee goals, performance reviews and their shared history.

Every change bumps the record's `version` (optimistic concurrency) and appends a PerformanceEvent with that version
for that record, so the same change cannot be recorded twice. Review content is confidential: the employee sees the
manager evaluation and rating only once a review is finalized, and `manager_private_notes` never.

Rating scale (overall_rating): 1 Needs significant improvement, 2 Partially meets expectations, 3 Meets
expectations, 4 Exceeds expectations, 5 Consistently exceeds expectations.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime, utcnow
from app.models.enums import GoalCategory, GoalStatus, PerformanceEventType, ReviewCycleStatus, ReviewStatus

if TYPE_CHECKING:
    from app.models.employee import Employee

RATING_SCALE = {1: "Needs significant improvement", 2: "Partially meets expectations", 3: "Meets expectations",
                4: "Exceeds expectations", 5: "Consistently exceeds expectations"}
ACTIVE_CYCLE_STATUSES = (ReviewCycleStatus.OPEN, ReviewCycleStatus.IN_PROGRESS)
ACTIVE_GOAL_STATUSES = (GoalStatus.NOT_STARTED, GoalStatus.IN_PROGRESS, GoalStatus.AT_RISK)
OPEN_REVIEW_STATUSES = (ReviewStatus.ASSIGNED, ReviewStatus.SELF_SUBMITTED, ReviewStatus.MANAGER_SUBMITTED)


class ReviewCycle(TimestampMixin, Base):
    __tablename__ = "performance_cycles"
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("period_start <= period_end", name="period_ordered"),
        CheckConstraint("self_assessment_deadline >= period_start AND self_assessment_deadline <= manager_review_deadline",
                        name="deadlines_ordered"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("status <> 'CANCELLED' OR cancellation_reason IS NOT NULL", name="cancelled_has_reason"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    self_assessment_deadline: Mapped[date] = mapped_column(Date, nullable=False)
    manager_review_deadline: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ReviewCycleStatus] = mapped_column(Enum(ReviewCycleStatus, name="review_cycle_status"), nullable=False,
                                                      index=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Goal(TimestampMixin, Base):
    __tablename__ = "performance_goals"
    __table_args__ = (
        CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name="progress_range"),
        CheckConstraint("start_date <= target_date", name="dates_ordered"),
        CheckConstraint("status <> 'COMPLETED' OR (progress_percent = 100 AND completed_at IS NOT NULL)",
                        name="completed_rule"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_performance_goals_employee_status", "employee_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("performance_cycles.id", ondelete="RESTRICT"), nullable=True,
                                                 index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    category: Mapped[GoalCategory] = mapped_column(Enum(GoalCategory, name="goal_category"), nullable=False)
    measurable_target: Mapped[str] = mapped_column(String(255), nullable=False)
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    progress_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    status: Mapped[GoalStatus] = mapped_column(Enum(GoalStatus, name="goal_status"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    manager_feedback: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    employee: Mapped["Employee"] = relationship()


class PerformanceReview(TimestampMixin, Base):
    __tablename__ = "performance_reviews"
    __table_args__ = (
        UniqueConstraint("cycle_id", "employee_id"),  # one review per employee per cycle
        CheckConstraint("overall_rating IS NULL OR (overall_rating >= 1 AND overall_rating <= 5)", name="rating_range"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("status NOT IN ('MANAGER_SUBMITTED', 'FINALIZED') OR overall_rating IS NOT NULL",
                        name="submitted_has_rating"),
        CheckConstraint("status <> 'FINALIZED' OR finalized_at IS NOT NULL", name="finalized_has_time"),
        CheckConstraint("status <> 'CANCELLED' OR cancellation_reason IS NOT NULL", name="cancelled_has_reason"),
        Index("ix_performance_reviews_employee_status", "employee_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("performance_cycles.id", ondelete="RESTRICT"), nullable=False)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[ReviewStatus] = mapped_column(Enum(ReviewStatus, name="review_status"), nullable=False, index=True)
    # Employee-owned
    self_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Reviewer-owned
    manager_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    improvement_areas: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    development_goals: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    training_recommendations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    manager_private_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    overall_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    manager_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Administrative
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finalized_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    employee: Mapped["Employee"] = relationship()
    cycle: Mapped[ReviewCycle] = relationship()


class PerformanceEvent(Base):
    """Append-only history for exactly one cycle, goal or review. `details` holds ids, field names, statuses and
    (for reviews) rating values, never free text; ratings are hidden from employees until finalization."""

    __tablename__ = "performance_events"
    __table_args__ = (
        CheckConstraint("(CASE WHEN cycle_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN goal_id IS NULL THEN 0 ELSE 1 END)"
                        " + (CASE WHEN review_id IS NULL THEN 0 ELSE 1 END) = 1", name="single_subject"),
        UniqueConstraint("cycle_id", "version"),
        UniqueConstraint("goal_id", "version"),
        UniqueConstraint("review_id", "version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("performance_cycles.id", ondelete="RESTRICT"), nullable=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("performance_goals.id", ondelete="RESTRICT"), nullable=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("performance_reviews.id", ondelete="RESTRICT"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[PerformanceEventType] = mapped_column(Enum(PerformanceEventType, name="performance_event_type"),
                                                             nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
