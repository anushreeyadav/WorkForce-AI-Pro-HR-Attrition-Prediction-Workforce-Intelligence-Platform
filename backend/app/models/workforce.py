"""Immutable workforce snapshots: aggregate counts captured from live records at a point in time.

Past headcount cannot be reconstructed reliably from current records, so snapshots are the only source
of historical headcount. They contain counts only (no names, emails, salaries or employee ids) and are
never updated or deleted. One snapshot per organization-local date.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UTCDateTime

_COUNTS_VALID = ("total_employees >= 0 AND active_employees >= 0 AND on_leave_employees >= 0 "
                 "AND inactive_employees >= 0 AND joiners_in_period >= 0 AND recorded_departures_in_period >= 0 "
                 "AND active_employees + on_leave_employees + inactive_employees = total_employees")


class WorkforceSnapshot(Base):
    __tablename__ = "workforce_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_date"),
        CheckConstraint(_COUNTS_VALID, name="counts_valid"),
        CheckConstraint("period_start <= snapshot_date", name="period_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)  # organization-local date
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    captured_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Flows below cover period_start (first day of the snapshot's month) .. snapshot_date, as known at capture.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    total_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    active_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    on_leave_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    inactive_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    joiners_in_period: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_departures_in_period: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    # Definitions and data-quality warnings (counts and text only).
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    departments: Mapped[list["WorkforceSnapshotDepartment"]] = relationship(
        back_populates="snapshot", order_by="WorkforceSnapshotDepartment.department_id"
    )


class WorkforceSnapshotDepartment(Base):
    __tablename__ = "workforce_snapshot_departments"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "department_id", name="uq_workforce_snapshot_departments_snapshot_id_department_id"),
        CheckConstraint(_COUNTS_VALID, name="counts_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("workforce_snapshots.id", ondelete="RESTRICT"), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True)
    department_name: Mapped[str] = mapped_column(String(100), nullable=False)  # name at capture time
    total_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    active_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    on_leave_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    inactive_employees: Mapped[int] = mapped_column(Integer, nullable=False)
    joiners_in_period: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_departures_in_period: Mapped[int] = mapped_column(Integer, nullable=False)

    snapshot: Mapped["WorkforceSnapshot"] = relationship(back_populates="departments")
