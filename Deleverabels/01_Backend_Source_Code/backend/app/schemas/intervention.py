"""Intervention schemas. Records describe agreed actions and factual follow-up, not conclusions about intent."""

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    InterventionCategory,
    InterventionEventType,
    InterventionPriority,
    InterventionStatus,
)
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.common import PaginationParams, trimmed

PositiveId = Annotated[int, Field(gt=0)]
Title = trimmed(3, 150)
Description = trimmed(1, 1000)
GUIDANCE = ("Record agreed actions and factual follow-up only. Do not record speculation about an employee's "
            "intentions, health or private life. Interventions never change employment status.")

CATEGORY_DESCRIPTIONS = {
    InterventionCategory.MANAGER_CHECK_IN: "A supportive one-to-one conversation with the manager",
    InterventionCategory.WORKLOAD_REVIEW: "Review workload, deadlines or staffing with the team",
    InterventionCategory.CAREER_DEVELOPMENT: "Discuss growth paths, goals or internal opportunities",
    InterventionCategory.RECOGNITION: "Acknowledge contributions through existing recognition practices",
    InterventionCategory.LEARNING_SUPPORT: "Offer training, mentoring or learning resources",
    InterventionCategory.WORK_ENVIRONMENT_REVIEW: "Review team or workplace conditions",
    InterventionCategory.OTHER_SUPPORT: "Another general support action",
}


class InterventionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: PositiveId
    risk_alert_id: PositiveId | None = Field(default=None, description="Optional; must be a visible alert for this employee")
    category: InterventionCategory
    title: Title
    description: Description | None = None
    priority: InterventionPriority = InterventionPriority.MEDIUM
    due_date: date | None = None
    assigned_to_user_id: PositiveId | None = None
    idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-]{8,64}$")] | None = Field(
        default=None, description="Client-generated key; retrying with it returns the original intervention")


class InterventionUpdate(BaseModel):
    """Editable while OPEN or IN_PROGRESS. Status, assignment and links have dedicated endpoints."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    category: InterventionCategory | None = None
    priority: InterventionPriority | None = None
    due_date: date | None = None
    expected_version: PositiveId | None = Field(default=None, description="Optional optimistic-concurrency check")

    @model_validator(mode="after")
    def _check(self) -> "InterventionUpdate":
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("Provide at least one field to update")
        for name in ("title", "category", "priority"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class VersionedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveId | None = None


class AssignRequest(VersionedAction):
    user_id: PositiveId


class CompleteRequest(VersionedAction):
    outcome: trimmed(5, 1000)


class CancelRequest(VersionedAction):
    reason: trimmed(5, 500)


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: trimmed(1, 2000)


class InterventionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee: AttendanceEmployeeRef
    risk_alert_id: int | None
    category: InterventionCategory
    title: str
    description: str | None
    priority: InterventionPriority
    status: InterventionStatus
    assigned_to_user_id: int | None
    due_date: date | None
    is_overdue: bool = False
    started_at: datetime | None
    completed_at: datetime | None
    outcome: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_by_user_id: int | None
    updated_by_user_id: int | None
    version: int
    created_at: datetime
    updated_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    event_type: InterventionEventType
    from_status: InterventionStatus | None
    to_status: InterventionStatus | None
    actor_user_id: int | None
    note: str | None
    details: dict[str, Any] | None
    created_at: datetime


class ListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: InterventionStatus | None = None
    priority: InterventionPriority | None = None
    category: InterventionCategory | None = None
    employee_id: PositiveId | None = None
    department_id: PositiveId | None = None
    risk_alert_id: PositiveId | None = None
    assigned_to_me: bool = False
    assigned_to_user_id: PositiveId | None = None
    q: trimmed(1, 100) | None = Field(default=None, description="Case-insensitive substring of the title")
    overdue: bool | None = None
    due_from: date | None = None
    due_to: date | None = None

    @model_validator(mode="after")
    def _range(self) -> "ListParams":
        if self.due_from and self.due_to and self.due_from > self.due_to:
            raise ValueError("due_from must be on or before due_to")
        return self


class Summary(BaseModel):
    by_status: dict[str, int]
    active_by_priority: dict[str, int]
    active_by_category: dict[str, int]
    overdue: int
    guidance: str = GUIDANCE
