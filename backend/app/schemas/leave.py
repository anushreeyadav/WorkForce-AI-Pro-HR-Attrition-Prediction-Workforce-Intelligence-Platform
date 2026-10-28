import re
from datetime import date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.models.enums import LeaveRequestStatus
from app.models.leave import MAX_LEAVE_DAYS
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.common import PaginationParams, display_name, trimmed
from app.schemas.department import Description

PositiveId = Annotated[int, Field(gt=0)]
DayCount = Annotated[int, Field(ge=0, le=MAX_LEAVE_DAYS)]
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")


def _normalize_code(value: str) -> str:
    value = value.upper()
    if not _CODE_PATTERN.fullmatch(value):
        raise ValueError("Code must be 2-20 characters: a letter, then letters, digits or underscores")
    return value


LeaveTypeName = display_name("Leave type name")
LeaveTypeCode = Annotated[trimmed(2, 20), AfterValidator(_normalize_code)]
Comment = trimmed(5, 500)


# --- Leave types ---------------------------------------------------------------------------------


class LeaveTypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: LeaveTypeName
    code: LeaveTypeCode
    description: Description = None
    is_paid: bool = True


class LeaveTypeUpdate(BaseModel):
    """The code is stable and cannot be changed."""

    model_config = ConfigDict(extra="forbid")

    name: LeaveTypeName | None = None
    description: Description = None
    is_paid: bool | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> "LeaveTypeUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for field in ("name", "is_paid", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class LeaveTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    description: str | None
    is_paid: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LeaveTypeRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class LeaveTypeListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = Field(default=None, description="ADMIN/HR only; others see active types")


# --- Policies -----------------------------------------------------------------------------------------


def validate_policy_dates(effective_from: date, effective_to: date | None) -> None:
    """Shared by create and by PATCH (after merging with stored values). Value ranges are field bounds."""
    if effective_to is not None and effective_to < effective_from:
        raise ValueError("effective_to must be on or after effective_from")


class LeavePolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leave_type_id: PositiveId
    department_id: PositiveId | None = Field(default=None, description="Omit for an organization-wide policy")
    annual_allowance_days: DayCount
    max_consecutive_days: Annotated[int, Field(gt=0, le=MAX_LEAVE_DAYS)] | None = None
    carry_forward_days: DayCount = 0
    count_weekends: bool = False
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self) -> "LeavePolicyCreate":
        validate_policy_dates(self.effective_from, self.effective_to)
        return self


class LeavePolicyUpdate(BaseModel):
    """leave_type_id and department_id are fixed; create a new policy instead."""

    model_config = ConfigDict(extra="forbid")

    annual_allowance_days: DayCount | None = None
    max_consecutive_days: Annotated[int, Field(gt=0, le=MAX_LEAVE_DAYS)] | None = None  # null removes the limit
    carry_forward_days: DayCount | None = None
    count_weekends: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None  # null = open-ended
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> "LeavePolicyUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for field in ("annual_allowance_days", "carry_forward_days", "count_weekends", "effective_from", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class LeavePolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    leave_type: LeaveTypeRef
    department_id: int | None
    annual_allowance_days: int
    max_consecutive_days: int | None
    carry_forward_days: int
    count_weekends: bool
    effective_from: date
    effective_to: date | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LeavePolicyListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    leave_type_id: PositiveId | None = None
    department_id: PositiveId | None = None
    is_active: bool | None = Field(default=None, description="ADMIN/HR only")


# --- Requests ---------------------------------------------------------------------------------------


class LeaveRequestCreate(BaseModel):
    """Self-service only: the employee is always the caller's linked record."""

    model_config = ConfigDict(extra="forbid")

    leave_type_id: PositiveId
    start_date: date
    end_date: date
    reason: Comment

    @model_validator(mode="after")
    def _check_dates(self) -> "LeaveRequestCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.start_date.year != self.end_date.year:
            raise ValueError("A leave request must fall within one calendar year; split it into two requests")
        return self


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: trimmed(1, 500) | None = None


class RejectDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: Comment = Field(description="Required: tell the employee why (5-500 characters)")


class CancelDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: Comment | None = Field(
        default=None, description="Required when ADMIN/HR cancel someone else's request"
    )


class LeaveRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee: AttendanceEmployeeRef
    leave_type: LeaveTypeRef
    policy_id: int
    start_date: date
    end_date: date
    working_days: int
    reason: str
    status: LeaveRequestStatus
    reviewed_by_user_id: int | None
    reviewed_at: datetime | None
    review_comment: str | None
    cancelled_by_user_id: int | None
    cancelled_at: datetime | None
    cancellation_comment: str | None
    created_at: datetime
    updated_at: datetime


class DateWindow(BaseModel):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _check_range(self) -> "DateWindow":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class MyLeaveParams(PaginationParams, DateWindow):
    model_config = ConfigDict(extra="forbid")

    status: LeaveRequestStatus | None = None
    leave_type_id: PositiveId | None = None


class LeaveReviewParams(PaginationParams, DateWindow):
    """Requests overlapping [date_from, date_to]."""

    model_config = ConfigDict(extra="forbid")

    status: LeaveRequestStatus | None = None
    employee_id: PositiveId | None = None
    department_id: PositiveId | None = None
    leave_type_id: PositiveId | None = None


# --- Balances and conflicts -----------------------------------------------------------------------


class LeaveBalanceRead(BaseModel):
    leave_type: LeaveTypeRef
    year: int
    allocated_days: int
    carried_forward_days: int
    used_days: int
    pending_days: int
    available_days: int
    policy_id: int | None = Field(description="Policy currently applicable to you; null if none applies")


class BalanceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=2000, le=2100)] | None = Field(
        default=None, description="Defaults to the current year in ORG_TIMEZONE"
    )


class LeaveConflictRead(BaseModel):
    attendance_id: int
    leave_request_id: int
    employee: AttendanceEmployeeRef
    work_date: date
    attendance_status: str
    leave_type: LeaveTypeRef


class ConflictParams(PaginationParams, DateWindow):
    model_config = ConfigDict(extra="forbid")

    employee_id: PositiveId | None = None
    department_id: PositiveId | None = None
