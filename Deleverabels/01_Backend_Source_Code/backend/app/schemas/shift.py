from datetime import datetime, time
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.models.shift import MAX_BREAK_MINUTES, MINUTES_PER_DAY
from app.schemas.common import PaginationParams, display_name, trimmed
from app.schemas.department import Description

ShiftName = display_name("Shift name")
BreakMinutes = Annotated[int, Field(ge=0, le=MAX_BREAK_MINUTES)]
OvertimeThreshold = Annotated[int, Field(gt=0, le=MINUTES_PER_DAY)]


def _validate_wall_time(value: time) -> time:
    if value.tzinfo is not None:
        raise ValueError("Shift times are wall-clock times in ORG_TIMEZONE; omit the UTC offset")
    if value.second or value.microsecond:
        raise ValueError("Shift times must be whole minutes (HH:MM)")
    return value


WallTime = Annotated[time, AfterValidator(_validate_wall_time)]


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def validate_shift_window(start: time, end: time, break_minutes: int) -> None:
    """Shared by create and by PATCH (after merging with stored values)."""
    if start == end:
        raise ValueError("start_time and end_time must differ")
    duration = (_minutes(end) - _minutes(start)) % MINUTES_PER_DAY
    if break_minutes >= duration:
        raise ValueError("break_minutes must be shorter than the shift")


class ShiftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ShiftName
    start_time: WallTime
    end_time: WallTime = Field(description="Earlier than start_time means the shift ends the next day")
    description: Description = None
    break_minutes: BreakMinutes = 0
    overtime_threshold_minutes: OvertimeThreshold = 480

    @model_validator(mode="after")
    def _check_window(self) -> "ShiftCreate":
        validate_shift_window(self.start_time, self.end_time, self.break_minutes)
        return self


class ShiftUpdate(BaseModel):
    """PATCH semantics; the merged result is re-validated against the stored shift."""

    model_config = ConfigDict(extra="forbid")

    name: ShiftName | None = None
    start_time: WallTime | None = None
    end_time: WallTime | None = None
    description: Description = None
    break_minutes: BreakMinutes | None = None
    overtime_threshold_minutes: OvertimeThreshold | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> "ShiftUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for field in ("name", "start_time", "end_time", "break_minutes", "overtime_threshold_minutes"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ShiftStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool
    reason: trimmed(1, 255) | None = None


class ShiftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    start_time: time
    end_time: time
    description: str | None
    break_minutes: int
    overtime_threshold_minutes: int
    is_overnight: bool
    duration_minutes: int
    scheduled_work_minutes: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ShiftRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ShiftListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = Field(
        default=None, description="ADMIN/HR only; other roles always receive active shifts"
    )
