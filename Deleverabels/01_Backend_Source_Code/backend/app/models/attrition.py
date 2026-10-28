"""Attrition model registry and prediction history. Separate from Employee: nothing here is reachable
from employee endpoints, and predictions never change employee, attendance, leave or payroll data.

Retention: rows are kept until an explicit retention job is introduced (none yet). Prediction rows hold
only the six schema features (no identifiers beyond employee_id) and a short explanation.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, Enum, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import ModelVersionStatus, RiskCategory

if TYPE_CHECKING:
    from app.models.employee import Employee


class AttritionModelVersion(TimestampMixin, Base):
    """One trained model. The artifact lives on disk; its SHA-256 here is checked before every load."""

    __tablename__ = "attrition_model_versions"
    __table_args__ = (UniqueConstraint("model_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[ModelVersionStatus] = mapped_column(
        Enum(ModelVersionStatus, name="model_version_status"), nullable=False, index=True
    )
    trained_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trained_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    training_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    # Evaluation metrics and training configuration (no employee data).
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    activated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # The exact registered dataset version used for training (nullable: models registered before the
    # dataset registry existed have none). RESTRICT: a version referenced by a model can never be deleted.
    dataset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )


class AttritionPrediction(Base):
    __tablename__ = "attrition_predictions"
    __table_args__ = (
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="risk_score_range"),
        Index("ix_attrition_predictions_employee_predicted", "employee_id", "predicted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("attrition_model_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    feature_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    risk_category: Mapped[RiskCategory] = mapped_column(
        Enum(RiskCategory, name="risk_category"), nullable=False, index=True
    )
    # Validated snapshot of the schema features only.
    features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # {"factors": [...], "missing_inputs": [...]}
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    predicted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    employee: Mapped["Employee"] = relationship()
    model: Mapped["AttritionModelVersion"] = relationship()
