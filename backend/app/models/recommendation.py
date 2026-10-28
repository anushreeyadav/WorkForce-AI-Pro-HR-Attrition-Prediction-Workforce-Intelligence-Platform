"""Persisted rule-based recommendations: the minimum needed for review history and deduplication.

Only actionable (AVAILABLE) outcomes are stored; INSUFFICIENT_DATA / SUPPRESSED rule outcomes are returned by
an evaluation but not persisted. Text is rule-generated (no feature values, no HR notes). Recommendations are
advisory: they never create interventions, change alerts or touch employee data by themselves.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, Enum, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import (
    RecommendationCategory,
    RecommendationPriority,
    RecommendationScope,
    RecommendationStatus,
)

if TYPE_CHECKING:
    from app.models.employee import Employee


class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        # rule_id:rule_version:subject:evidence_window. Re-evaluating the same evidence never recreates a
        # recommendation (even a dismissed one); a new window (new alert, new month) may.
        UniqueConstraint("dedup_key"),
        CheckConstraint(
            "(scope = 'INDIVIDUAL' AND employee_id IS NOT NULL) OR "
            "(scope = 'DEPARTMENT' AND employee_id IS NULL AND department_id IS NOT NULL) OR "
            "(scope = 'ORGANIZATION' AND employee_id IS NULL AND department_id IS NULL)",
            name="scope_subject"),
        CheckConstraint("status <> 'DISMISSED' OR dismissal_reason IS NOT NULL", name="dismissed_has_reason"),
        CheckConstraint("status <> 'COMPLETED' OR completion_outcome IS NOT NULL", name="completed_has_outcome"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("rank_score IS NULL OR (rank_score >= 0 AND rank_score <= 100)", name="rank_score_range"),
        Index("ix_recommendations_scope_status", "scope", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    rule_version: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[RecommendationScope] = mapped_column(Enum(RecommendationScope, name="recommendation_scope"), nullable=False)
    category: Mapped[RecommendationCategory] = mapped_column(Enum(RecommendationCategory, name="recommendation_category"), nullable=False)
    priority: Mapped[RecommendationPriority] = mapped_column(Enum(RecommendationPriority, name="recommendation_priority"), nullable=False)
    status: Mapped[RecommendationStatus] = mapped_column(Enum(RecommendationStatus, name="recommendation_status"),
                                                         nullable=False, index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=True, index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True, index=True)
    risk_alert_id: Mapped[int | None] = mapped_column(ForeignKey("risk_alerts.id", ondelete="RESTRICT"), nullable=True, index=True)
    intervention_id: Mapped[int | None] = mapped_column(ForeignKey("interventions.id", ondelete="RESTRICT"), nullable=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    suggested_action: Mapped[str] = mapped_column(String(500), nullable=False)
    explanation: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Record references (ids) and aggregate metric values only.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_window: Mapped[str] = mapped_column(String(60), nullable=False)
    source_data_as_of: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    dismissal_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completion_outcome: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # rules-v2 additions (NULL for rules-v1 records, which are never relabelled).
    # use_alter: tasks.recommendation_id points back here, so this key is added after both tables exist.
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT", use_alter=True),
                                                nullable=True, index=True)
    # Deterministic ordering score 0-100 (see recommendation_rules_v2.rank). NOT a probability or confidence.
    rank_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    priority_reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Hash of the material evidence; a change supersedes the earlier recommendation.
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id", ondelete="RESTRICT"),
                                                         nullable=True)

    employee: Mapped["Employee | None"] = relationship()

# Activity-feed query indexes (see README, "Feed query indexes"): each serves a range/keyset filter on this
# timestamp with ORDER BY (timestamp, id) LIMIT n. InnoDB secondary indexes include the primary key.
Index("ix_recommendations_evaluated_at", Recommendation.evaluated_at)
Index("ix_recommendations_reviewed_at", Recommendation.reviewed_at)
