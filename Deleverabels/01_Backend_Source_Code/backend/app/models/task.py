"""Workforce tasks: operational HR follow-up work assigned to employees, with versioned history.

Tasks are workflow records only. Completing a task linked to an intervention or recommendation does not change that
record and is not evidence that it succeeded. Every change bumps `version` (optimistic concurrency) and appends a
TaskEvent with that version, so the same change can never be recorded twice.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, Date, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime, utcnow
from app.models.enums import TaskBlockReason, TaskEventType, TaskPriority, TaskStatus, TaskType

if TYPE_CHECKING:
    from app.models.employee import Employee

ACTIVE_TASK_STATUSES = (TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("intervention_id IS NULL OR recommendation_id IS NULL", name="single_source"),
        CheckConstraint("status <> 'COMPLETED' OR completed_at IS NOT NULL", name="completed_has_time"),
        CheckConstraint("status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL)",
                        name="cancelled_has_reason"),
        CheckConstraint("status <> 'BLOCKED' OR block_reason IS NOT NULL", name="blocked_has_reason"),
        Index("ix_tasks_department_status", "department_id", "status"),
        Index("ix_tasks_assignee_status", "assignee_employee_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="task_type"), nullable=False)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority, name="task_priority"), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus, name="task_status"), nullable=False)
    # Creator is the signed-in user (ADMIN/HR accounts need not have an employee record).
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
                                                           index=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assignee_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=True)
    # Scope of the task. NULL = organization-level (ADMIN/HR only, plus the assignee).
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)  # organization-local date
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completion_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    block_reason: Mapped[TaskBlockReason | None] = mapped_column(Enum(TaskBlockReason, name="task_block_reason"),
                                                                 nullable=True)
    block_details: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Optional source workflow item (at most one).
    intervention_id: Mapped[int | None] = mapped_column(ForeignKey("interventions.id", ondelete="RESTRICT"), nullable=True,
                                                        index=True)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id", ondelete="RESTRICT"),
                                                          nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    assignee: Mapped["Employee | None"] = relationship()


class TaskEvent(Base):
    """Append-only task history. `details` holds ids, field names and codes only, never free text."""

    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_task_events_task_id_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[TaskEventType] = mapped_column(Enum(TaskEventType, name="task_event_type"), nullable=False)
    from_status: Mapped[TaskStatus | None] = mapped_column(Enum(TaskStatus, name="task_status"), nullable=True)
    to_status: Mapped[TaskStatus | None] = mapped_column(Enum(TaskStatus, name="task_status"), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
