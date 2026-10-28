"""Performance management schemas. Status changes are named actions; clients never submit a status for cycles or
reviews. Review responses are shaped per viewer by the router (see app/api/performance.py)."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import GoalCategory, GoalStatus, PerformanceEventType, ReviewCycleStatus, ReviewStatus
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.common import PaginationParams, trimmed

PositiveId = Annotated[int, Field(gt=0)]
Version = Annotated[int, Field(gt=0)]
LongText = trimmed(1, 8000)
Item = trimmed(1, 300)
Items = Annotated[list[Item], Field(max_length=10)]
Rating = Annotated[int, Field(ge=1, le=5)]
MAX_MANAGER_DEADLINE_AFTER_PERIOD = timedelta(days=365)


def check_cycle_dates(period_start: date, period_end: date, self_deadline: date, manager_deadline: date) -> None:
    if period_start > period_end:
        raise ValueError("period_start must be on or before period_end")
    if not period_start <= self_deadline <= manager_deadline:
        raise ValueError("Deadlines must satisfy period_start <= self_assessment_deadline <= manager_review_deadline")
    if manager_deadline > period_end + MAX_MANAGER_DEADLINE_AFTER_PERIOD:
        raise ValueError("manager_review_deadline may be at most 365 days after period_end")
    if (period_end - period_start).days > 731:
        raise ValueError("A review period may cover at most two years")


# --- Cycles -------------------------------------------------------------------------------------------------


class CycleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: trimmed(3, 120)
    description: trimmed(1, 2000) | None = None
    period_start: date
    period_end: date
    self_assessment_deadline: date
    manager_review_deadline: date

    @model_validator(mode="after")
    def _dates(self) -> "CycleCreate":
        check_cycle_dates(self.period_start, self.period_end, self.self_assessment_deadline, self.manager_review_deadline)
        return self


class CycleUpdate(BaseModel):
    """DRAFT: every field. OPEN / IN_PROGRESS: description and deadlines only. Final cycles: nothing."""

    model_config = ConfigDict(extra="forbid")

    name: trimmed(3, 120) | None = None
    description: trimmed(1, 2000) | None = None
    period_start: date | None = None
    period_end: date | None = None
    self_assessment_deadline: date | None = None
    manager_review_deadline: date | None = None
    expected_version: Version | None = None


class CycleTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["open", "start_reviews", "complete", "cancel"]
    cancellation_reason: trimmed(5, 500) | None = None
    expected_version: Version | None = None

    @model_validator(mode="after")
    def _reason(self) -> "CycleTransition":
        if (self.action == "cancel") != (self.cancellation_reason is not None):
            raise ValueError("cancellation_reason is required for cancel and not allowed otherwise")
        return self


class CycleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    period_start: date
    period_end: date
    self_assessment_deadline: date
    manager_review_deadline: date
    status: ReviewCycleStatus
    cancellation_reason: str | None
    created_by_user_id: int | None
    version: int
    created_at: datetime
    updated_at: datetime


class CycleListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: ReviewCycleStatus | None = None
    period_from: date | None = Field(default=None, description="Cycles ending on or after this date")
    period_to: date | None = Field(default=None, description="Cycles starting on or before this date")


# --- Goals --------------------------------------------------------------------------------------------------


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: PositiveId | None = Field(default=None, description="Default: yourself")
    cycle_id: PositiveId | None = None
    title: trimmed(3, 150)
    description: trimmed(1, 2000) | None = None
    category: GoalCategory = GoalCategory.BUSINESS
    measurable_target: trimmed(3, 255)
    target_value: Annotated[Decimal, Field(ge=0, le=Decimal("9999999999.99"), decimal_places=2)] | None = None
    unit: trimmed(1, 30) | None = None
    start_date: date
    target_date: date
    progress_percent: Annotated[int, Field(ge=0, le=100)] = 0

    @model_validator(mode="after")
    def _dates(self) -> "GoalCreate":
        if self.start_date > self.target_date:
            raise ValueError("start_date must be on or before target_date")
        if (self.target_date - self.start_date).days > 1096:
            raise ValueError("A goal may span at most three years")
        return self


class GoalUpdate(BaseModel):
    """Owners: content, dates, progress and status (not CANCELLED) of their active goals. Managers of the
    employee: additionally manager_feedback, cancellation, reopening finished goals and reassignment."""

    model_config = ConfigDict(extra="forbid")

    title: trimmed(3, 150) | None = None
    description: trimmed(1, 2000) | None = None
    category: GoalCategory | None = None
    measurable_target: trimmed(3, 255) | None = None
    target_value: Annotated[Decimal, Field(ge=0, le=Decimal("9999999999.99"), decimal_places=2)] | None = None
    unit: trimmed(1, 30) | None = None
    start_date: date | None = None
    target_date: date | None = None
    progress_percent: Annotated[int, Field(ge=0, le=100)] | None = None
    status: GoalStatus | None = None
    manager_feedback: trimmed(1, 2000) | None = None
    employee_id: PositiveId | None = Field(default=None, description="Reassign (managers only)")
    expected_version: Version | None = None

    @model_validator(mode="after")
    def _no_null_required(self) -> "GoalUpdate":
        for name in ("title", "category", "measurable_target", "start_date", "target_date", "progress_percent", "status",
                     "employee_id"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared")
        return self


class GoalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee: AttendanceEmployeeRef
    cycle_id: int | None
    title: str
    description: str | None
    category: GoalCategory
    measurable_target: str
    target_value: Decimal | None
    unit: str | None
    start_date: date
    target_date: date
    progress_percent: int
    status: GoalStatus
    completed_at: datetime | None
    manager_feedback: str | None
    created_by_user_id: int | None
    version: int
    created_at: datetime
    updated_at: datetime
    is_overdue: bool = False
    can_manage: bool = False


class GoalListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    employee_id: PositiveId | None = None
    department_id: PositiveId | None = None
    cycle_id: PositiveId | None = None
    status: GoalStatus | None = None
    category: GoalCategory | None = None
    target_from: date | None = None
    target_to: date | None = None
    mine: bool = False
    q: trimmed(1, 100) | None = Field(default=None, description="Case-insensitive substring of the goal title")


# --- Reviews ------------------------------------------------------------------------------------------------


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: PositiveId
    employee_id: PositiveId
    reviewer_user_id: PositiveId


class ManagerFields(BaseModel):
    manager_summary: LongText | None = None
    strengths: Items | None = None
    improvement_areas: Items | None = None
    development_goals: Items | None = None
    training_recommendations: Items | None = None
    manager_private_notes: trimmed(1, 2000) | None = None
    overall_rating: Rating | None = None


MANAGER_FIELDS = tuple(ManagerFields.model_fields)


class ReviewUpdate(ManagerFields):
    """The reviewed employee may set only self_assessment; the assigned reviewer only the evaluation fields;
    ADMIN/HR (or the employee's department manager) only reviewer_user_id."""

    model_config = ConfigDict(extra="forbid")

    self_assessment: LongText | None = None
    reviewer_user_id: PositiveId | None = None
    expected_version: Version | None = None


class SelfAssessmentSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    self_assessment: LongText | None = Field(default=None, description="Saved before submitting, if given")
    expected_version: Version | None = None


class ManagerReviewSubmit(ManagerFields):
    model_config = ConfigDict(extra="forbid")

    expected_version: Version | None = None


class VersionOnly(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Version | None = None


class ReviewReopen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: trimmed(10, 500)
    expected_version: Version | None = None


class ReviewCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: trimmed(5, 500)
    expected_version: Version | None = None


class ReviewRead(BaseModel):
    id: int
    cycle_id: int
    employee: AttendanceEmployeeRef
    reviewer_user_id: int
    status: ReviewStatus
    self_assessment: str | None
    self_submitted_at: datetime | None
    manager_evaluation_visible: bool = Field(description="False for the reviewed employee until the review is finalized")
    manager_summary: str | None
    strengths: list[str] | None
    improvement_areas: list[str] | None
    development_goals: list[str] | None
    training_recommendations: list[str] | None
    manager_private_notes: str | None = Field(description="Only the reviewer and ADMIN/HR; never the employee")
    overall_rating: int | None
    rating_label: str | None
    manager_submitted_at: datetime | None
    finalized_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    reopen_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    your_role: Literal["employee", "reviewer", "administrator", "department_manager"]


class ReviewListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    cycle_id: PositiveId | None = None
    employee_id: PositiveId | None = None
    reviewer_user_id: PositiveId | None = None
    department_id: PositiveId | None = None
    status: ReviewStatus | None = None
    to_review: bool = Field(default=False, description="Only reviews where you are the reviewer")
    mine: bool = Field(default=False, description="Only reviews about you")


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    event_type: PerformanceEventType
    from_status: str | None
    to_status: str | None
    actor_user_id: int | None
    details: dict[str, Any] | None
    created_at: datetime


class PerformanceSummary(BaseModel):
    cycles_by_status: dict[str, int]
    reviews_by_status: dict[str, int]
    finalized_rating_distribution: dict[str, int] | None = Field(
        description="Counts of finalized ratings 1-5 (null for employees)")
    goals_by_status: dict[str, int]
    active_goals_average_progress: float | None
    goals_at_risk_or_overdue: int
    reviews_awaiting_you: int
    rating_scale: dict[str, str]
