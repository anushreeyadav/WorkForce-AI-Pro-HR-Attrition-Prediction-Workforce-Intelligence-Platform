from datetime import date, datetime, timedelta
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AttendanceStatus
from app.schemas.common import PaginationParams, trimmed
from app.schemas.shift import ShiftRef

MAX_RANGE_DAYS = 366
PositiveId = Annotated[int, Field(gt=0)]
Note = trimmed(1, 500)
CorrectionBreak = Annotated[int, Field(ge=0, le=1440)]


# --- Self-service requests (timestamps are always server-side) -------------------------------------


class CheckInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shift_id: PositiveId | None = Field(default=None, description="An active shift; omit for unscheduled work")
    note: Note | None = None


class CheckOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    break_minutes: Annotated[int, Field(ge=0, le=1440)] | None = Field(
        default=None, description="Recorded break; defaults to the shift's break, or 0 without a shift"
    )
    note: Note | None = None


class AttendanceCorrection(BaseModel):
    """ADMIN/HR correction. Calculated fields are never accepted; they are recomputed on the server."""

    model_config = ConfigDict(extra="forbid")

    check_in: AwareDatetime | None = None
    check_out: AwareDatetime | None = None
    break_minutes: CorrectionBreak | None = None
    shift_id: PositiveId | None = None  # explicit null removes the shift
    status: AttendanceStatus | None = Field(
        default=None, description="Override; when omitted the status is recalculated"
    )
    reason: trimmed(5, 500)

    @model_validator(mode="after")
    def _check_fields(self) -> "AttendanceCorrection":
        changes = self.model_fields_set - {"reason"}
        if not changes:
            raise ValueError("Provide at least one field to correct")
        for field in ("check_in", "check_out", "break_minutes", "status"):
            if field in changes and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


# --- Responses ---------------------------------------------------------------------------------------


class AttendanceEmployeeRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_code: str
    full_name: str
    department_id: int


class AttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee: AttendanceEmployeeRef
    shift: ShiftRef | None
    work_date: date
    check_in: datetime | None
    check_out: datetime | None
    break_minutes: int
    work_minutes: int | None
    overtime_minutes: int | None
    status: AttendanceStatus
    is_open: bool
    note: str | None
    created_at: datetime
    updated_at: datetime


class AttendanceSummary(BaseModel):
    date_from: date | None
    date_to: date | None
    records: int = Field(description="Attendance records in range (missing days are not counted)")
    attended_days: int = Field(description="Records with status PRESENT, LATE or HALF_DAY")
    present_days: int
    late_days: int
    half_days: int
    absent_days: int = Field(description="Explicitly recorded ABSENT records only")
    on_leave_days: int
    open_records: int = Field(description="Checked in but not yet checked out")
    work_minutes: int
    overtime_minutes: int
    attendance_percentage: float | None = Field(
        description="attended_days / (attended_days + absent_days) × 100; null when the denominator is 0. "
        "ON_LEAVE records and days with no record are excluded."
    )


# --- Query parameters ----------------------------------------------------------------------------


class DateRange(BaseModel):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _check_range(self) -> "DateRange":
        if self.date_from and self.date_to:
            if self.date_from > self.date_to:
                raise ValueError("date_from must be on or before date_to")
            if self.date_to - self.date_from > timedelta(days=MAX_RANGE_DAYS):
                raise ValueError(f"Date range cannot exceed {MAX_RANGE_DAYS} days")
        return self


class MyAttendanceParams(PaginationParams, DateRange):
    model_config = ConfigDict(extra="forbid")

    status: AttendanceStatus | None = None


class MySummaryParams(DateRange):
    model_config = ConfigDict(extra="forbid")


class AttendanceReportFilters(DateRange):
    model_config = ConfigDict(extra="forbid")

    employee_id: PositiveId | None = None
    department_id: PositiveId | None = None
    shift_id: PositiveId | None = None
    status: AttendanceStatus | None = None


class AttendanceReportParams(PaginationParams, AttendanceReportFilters):
    model_config = ConfigDict(extra="forbid")
