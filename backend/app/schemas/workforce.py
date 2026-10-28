"""Workforce analytics schemas. Aggregates only: no names, emails, employee ids, salaries or risk features."""

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PaginationParams

PositiveId = Annotated[int, Field(gt=0)]
OBSERVED_NOTE = "Observed from employee records, the employee status audit trail and captured snapshots."
ESTIMATE_NOTE = ("Workforce-size projection from a linear trend of observed month-end headcount. It is an estimate, "
                 "not a prediction of which employees will leave, and it uses no individual risk scores.")


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_date: date | None = Field(default=None, description="Must be today (org time zone) if given")


class DepartmentCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    department_id: int
    department_name: str
    total_employees: int
    active_employees: int
    on_leave_employees: int
    inactive_employees: int
    joiners_in_period: int
    recorded_departures_in_period: int


class SnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    snapshot_date: date
    captured_at: datetime
    period_start: date
    total_employees: int
    active_employees: int
    on_leave_employees: int
    inactive_employees: int
    joiners_in_period: int
    recorded_departures_in_period: int
    source: str
    data_quality: dict[str, Any]
    departments: list[DepartmentCounts]


class DateRange(BaseModel):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _check(self) -> "DateRange":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class SnapshotListParams(PaginationParams, DateRange):
    model_config = ConfigDict(extra="forbid")


class ScopeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: PositiveId | None = Field(default=None, description="Managers are always limited to their department")


class StabilityParams(ScopeParams):
    model_config = ConfigDict(extra="forbid")

    by_department: bool = Field(default=False, description="Also return the indicator for every department in your "
                                "scope (managers: their own department only)")


class TrendParams(ScopeParams, DateRange):
    model_config = ConfigDict(extra="forbid")


class ForecastParams(ScopeParams):
    model_config = ConfigDict(extra="forbid")

    horizon_months: Annotated[int, Field(ge=1, le=36)] = 6


class RetentionParams(ScopeParams, DateRange):
    model_config = ConfigDict(extra="forbid")


class DepartmentDetailParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_months: Annotated[int, Field(ge=1, le=36)] = 6


class Envelope(BaseModel):
    """Common shape: scope, the data, and the definitions needed to read it."""

    scope: dict[str, Any]
    data: dict[str, Any]
    definitions: dict[str, str] = Field(default_factory=dict)
