"""Workforce task schemas. Clients never set `status` directly: status changes go through named transition actions."""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import TaskBlockReason, TaskEventType, TaskPriority, TaskStatus, TaskType
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.common import PaginationParams, trimmed

PositiveId = Annotated[int, Field(gt=0)]
Title = trimmed(3, 150)
Description = trimmed(1, 2000)
Reason = trimmed(5, 500)
Summary = trimmed(1, 2000)
TransitionAction = Literal["start", "block", "unblock", "complete", "cancel", "reopen"]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description | None = None
    task_type: TaskType = TaskType.FOLLOW_UP
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: date | None = Field(default=None, description="Organization-local date, today to 366 days ahead")
    assignee_employee_id: PositiveId | None = None
    department_id: PositiveId | None = Field(default=None, description="Default: the source's, the assignee's or your department")
    intervention_id: PositiveId | None = Field(default=None, description="Optional source; must be visible to you")
    recommendation_id: PositiveId | None = Field(default=None, description="Optional source; must be visible to you")
    idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-]{8,64}$")] | None = Field(
        default=None, description="Client-generated key; retrying with it returns the original task")

    @model_validator(mode="after")
    def _single_source(self) -> "TaskCreate":
        if self.intervention_id is not None and self.recommendation_id is not None:
            raise ValueError("Link at most one source: intervention_id or recommendation_id")
        return self


class TaskUpdate(BaseModel):
    """Task managers only, while OPEN, IN_PROGRESS or BLOCKED. Assignment and status have dedicated endpoints."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    task_type: TaskType | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    expected_version: PositiveId | None = Field(default=None, description="Optional optimistic-concurrency check")

    @model_validator(mode="after")
    def _no_null_required(self) -> "TaskUpdate":
        for name in ("title", "task_type", "priority"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared")
        return self


class TaskAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_employee_id: PositiveId
    expected_version: PositiveId | None = None


class TaskTransition(BaseModel):
    """`block` needs block_reason (block_details optional); `cancel` needs cancellation_reason; `complete` may carry
    completion_summary. Other fields are rejected for actions that do not use them."""

    model_config = ConfigDict(extra="forbid")

    action: TransitionAction
    block_reason: TaskBlockReason | None = None
    block_details: Reason | None = None
    cancellation_reason: Reason | None = None
    completion_summary: Summary | None = None
    expected_version: PositiveId | None = None

    @model_validator(mode="after")
    def _fields_match_action(self) -> "TaskTransition":
        allowed = {"block": {"block_reason", "block_details"}, "cancel": {"cancellation_reason"},
                   "complete": {"completion_summary"}}.get(self.action, set())
        given = {name for name in ("block_reason", "block_details", "cancellation_reason", "completion_summary")
                 if getattr(self, name) is not None}
        if given - allowed:
            raise ValueError(f"{', '.join(sorted(given - allowed))} not allowed for action {self.action}")
        if self.action == "block" and self.block_reason is None:
            raise ValueError("block_reason is required to block a task")
        if self.action == "cancel" and self.cancellation_reason is None:
            raise ValueError("cancellation_reason is required to cancel a task")
        return self


class TaskEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    event_type: TaskEventType
    from_status: TaskStatus | None
    to_status: TaskStatus | None
    actor_user_id: int | None
    details: dict[str, Any] | None
    created_at: datetime


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    task_type: TaskType
    priority: TaskPriority
    status: TaskStatus
    assignee: AttendanceEmployeeRef | None
    department_id: int | None
    created_by_user_id: int | None
    due_date: date | None
    is_overdue: bool = False
    started_at: datetime | None
    completed_at: datetime | None
    completion_summary: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    block_reason: TaskBlockReason | None
    block_details: str | None
    intervention_id: int | None
    recommendation_id: int | None
    version: int
    created_at: datetime
    updated_at: datetime
    can_manage: bool = Field(default=False, description="Whether you may edit, reassign, cancel or reopen it")


class TaskDetail(TaskRead):
    recent_history: list[TaskEventRead] = Field(default_factory=list, description="Latest 20 events, newest first")


class TaskListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    task_type: TaskType | None = None
    assignee_employee_id: PositiveId | None = None
    created_by_user_id: PositiveId | None = None
    department_id: PositiveId | None = None
    intervention_id: PositiveId | None = None
    recommendation_id: PositiveId | None = None
    assigned_to_me: bool = False
    q: trimmed(1, 100) | None = Field(default=None, description="Case-insensitive substring of the title")
    overdue: bool | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort: Literal["updated", "due", "created", "priority"] = Field(
        default="updated", description="updated/created: newest first; due: soonest first (no due date last); "
                                       "priority: CRITICAL first. Ties broken by id.")

    @model_validator(mode="after")
    def _due_range(self) -> "TaskListParams":
        if self.due_from and self.due_to and self.due_from > self.due_to:
            raise ValueError("due_from must be on or before due_to")
        return self


class TaskSummary(BaseModel):
    by_status: dict[str, int]
    active_by_priority: dict[str, int]
    active_by_type: dict[str, int]
    overdue: int
    due_within_7_days: int
    assigned_to_me_active: int
    unassigned_active: int
