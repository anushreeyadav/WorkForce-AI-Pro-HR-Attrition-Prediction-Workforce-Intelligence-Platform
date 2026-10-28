from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PaginationParams, display_name, trimmed


def _blank_to_none(value: str | None) -> str | None:
    return value or None


DepartmentName = display_name("Department name")
Description = Annotated[trimmed(0, 1000) | None, AfterValidator(_blank_to_none)]


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DepartmentName
    description: Description = None
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    """PATCH semantics: only fields present in the request body are changed."""

    model_config = ConfigDict(extra="forbid")

    name: DepartmentName | None = None
    description: Description = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> "DepartmentUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for field in ("name", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class DepartmentRead(BaseModel):
    id: int
    name: str
    description: str | None
    is_active: bool
    employee_count: int
    created_at: datetime
    updated_at: datetime


class DepartmentRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class DepartmentListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    q: trimmed(1, 100) | None = Field(default=None, description="Case-insensitive substring of the department name")
