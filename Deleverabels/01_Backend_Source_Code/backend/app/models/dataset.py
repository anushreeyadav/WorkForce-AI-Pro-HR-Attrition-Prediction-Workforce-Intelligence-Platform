"""Workforce dataset registry. Only metadata and validation summaries are stored here; file contents
live in ML_DATASET_DIR under server-generated names and are never copied into the database.

A DatasetVersion's file is immutable: new content always means a new version. Versions are never
deleted (models keep a reference to the exact version they were trained on).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, Enum, ForeignKey, Integer, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import DatasetPurpose, DatasetStatus


class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[DatasetPurpose] = mapped_column(Enum(DatasetPurpose, name="dataset_purpose"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    archived_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    versions: Mapped[list["DatasetVersion"]] = relationship(
        back_populates="dataset", order_by="DatasetVersion.version_number"
    )


class DatasetVersion(TimestampMixin, Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dataset_versions_dataset_id_version_number"),
        UniqueConstraint("storage_key"),
        # Identical content can be registered only once (across all datasets).
        UniqueConstraint("sha256"),
        CheckConstraint("version_number >= 1", name="version_positive"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("row_count >= 0 AND column_count > 0", name="shape_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Server-generated (uuid4 hex); the file is <ML_DATASET_DIR>/<storage_key>.csv. Never client-supplied.
    storage_key: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DatasetStatus] = mapped_column(Enum(DatasetStatus, name="dataset_status"), nullable=False, index=True)
    # Feature-schema version the report was produced against (null until validated).
    schema_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    training_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Counts, column names, error codes and row numbers only: never cell values.
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    dataset: Mapped["Dataset"] = relationship(back_populates="versions")
