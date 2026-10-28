import re
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.models.enums import EmploymentStatus
from app.schemas.auth import NormalizedEmail
from app.schemas.common import PaginationParams, trimmed
from app.schemas.department import DepartmentRef

EARLIEST_JOINING_DATE = date(1950, 1, 1)
MAX_FUTURE_JOINING_DAYS = 366

_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,19}$")
_PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 ()\-]{5,18}[0-9]$")


def _normalize_code(value: str) -> str:
    value = value.upper()
    if not _CODE_PATTERN.fullmatch(value):
        raise ValueError("Employee code must be 2-20 characters: letters, digits and hyphens")
    return value


def _validate_phone(value: str | None) -> str | None:
    if not value:
        return None
    if not _PHONE_PATTERN.fullmatch(value):
        raise ValueError("Phone may contain digits, spaces, ( ) - and an optional leading +")
    return value


def _validate_joining_date(value: date) -> date:
    if value < EARLIEST_JOINING_DATE:
        raise ValueError(f"Joining date cannot be before {EARLIEST_JOINING_DATE.isoformat()}")
    if value > date.today() + timedelta(days=MAX_FUTURE_JOINING_DAYS):
        raise ValueError("Joining date cannot be more than a year in the future")
    return value


EmployeeCode = Annotated[trimmed(2, 20), AfterValidator(_normalize_code)]
PersonName = trimmed(1, 150)
Designation = trimmed(1, 100)
Phone = Annotated[trimmed(0, 20) | None, AfterValidator(_validate_phone)]
JoiningDate = Annotated[date, AfterValidator(_validate_joining_date)]
PositiveId = Annotated[int, Field(gt=0)]

# Fields that exist on Employee but may not be set to null through PATCH.
_NON_NULLABLE_UPDATE_FIELDS = (
    "employee_code", "full_name", "work_email", "department_id", "designation", "date_of_joining",
)


class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_code: EmployeeCode
    full_name: PersonName
    work_email: NormalizedEmail
    department_id: PositiveId
    designation: Designation
    date_of_joining: JoiningDate
    status: EmploymentStatus = EmploymentStatus.ACTIVE
    phone: Phone = None
    user_id: PositiveId | None = None


class EmployeeUpdate(BaseModel):
    """PATCH semantics: omitted fields are untouched. Status changes use the dedicated /status endpoint."""

    model_config = ConfigDict(extra="forbid")

    employee_code: EmployeeCode | None = None
    full_name: PersonName | None = None
    work_email: NormalizedEmail | None = None
    department_id: PositiveId | None = None
    designation: Designation | None = None
    date_of_joining: JoiningDate | None = None
    phone: Phone = None
    user_id: PositiveId | None = None  # null unlinks the login account

    @model_validator(mode="after")
    def _check_fields(self) -> "EmployeeUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for field in _NON_NULLABLE_UPDATE_FIELDS:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class EmployeeStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EmploymentStatus
    reason: trimmed(1, 255) | None = None


class EmployeeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_code: str
    full_name: str
    work_email: str
    designation: str
    date_of_joining: date
    status: EmploymentStatus
    phone: str | None
    user_id: int | None
    department: DepartmentRef
    created_at: datetime
    updated_at: datetime


SortField = Literal["full_name", "employee_code", "date_of_joining", "created_at"]


class EmployeeListParams(PaginationParams):
    """Query parameters for GET /api/employees. Unknown parameters are rejected with 422."""

    model_config = ConfigDict(extra="forbid")

    q: trimmed(1, 100) | None = Field(
        default=None, description="Matches employee code, full name, work email or designation"
    )
    department_id: PositiveId | None = None
    status: EmploymentStatus | None = None
    designation: trimmed(1, 100) | None = Field(default=None, description="Exact match, case-insensitive")
    joined_from: date | None = None
    joined_to: date | None = None
    user_active: bool | None = Field(
        default=None,
        description="true/false: only employees whose linked login account is active/inactive",
    )
    sort_by: SortField = "full_name"
    sort_order: Literal["asc", "desc"] = "asc"

    @model_validator(mode="after")
    def _check_date_range(self) -> "EmployeeListParams":
        if self.joined_from and self.joined_to and self.joined_from > self.joined_to:
            raise ValueError("joined_from must be on or before joined_to")
        return self


class EmployeeSearchParams(EmployeeListParams):
    """GET /api/employees/search: like the list, but a search term of at least 2 characters is required."""

    q: trimmed(2, 100) = Field(description="Matches employee code, full name, work email or designation")


class DepartmentEmployeeParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: EmploymentStatus | None = None
