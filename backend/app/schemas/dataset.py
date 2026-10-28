"""Dataset registry schemas. Nothing here carries row contents: only metadata, counts and issue codes."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DatasetPurpose, DatasetStatus
from app.schemas.common import PaginationParams, display_name, trimmed

DatasetName = display_name("Dataset name")
PositiveInt = Annotated[int, Field(gt=0)]


class DatasetVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_number: int
    status: DatasetStatus
    sha256: str
    size_bytes: int
    row_count: int
    column_count: int
    schema_version: str | None
    training_eligible: bool
    validated_at: datetime | None
    uploaded_by_user_id: int | None
    created_at: datetime
    used_by_models: list[str] = Field(default_factory=list, description="Model versions trained on this version")


class DatasetRead(BaseModel):
    id: int
    display_name: str
    purpose: DatasetPurpose
    created_by_user_id: int | None
    created_at: datetime
    archived_at: datetime | None
    version_count: int
    latest_version: DatasetVersionRead


class DatasetListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    include_archived: bool = False
    status: DatasetStatus | None = Field(default=None, description="Status of the latest version")
    q: trimmed(1, 100) | None = Field(default=None, description="Case-insensitive substring of the dataset name")


class VersionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: PositiveInt | None = Field(default=None, description="Defaults to the latest version")


class ValidationReportRead(BaseModel):
    dataset_id: int
    version_number: int
    status: DatasetStatus
    training_eligible: bool
    report: dict[str, Any] | None = Field(
        description="Counts, column names, row numbers and issue codes only; never cell values"
    )


class ArchiveResult(BaseModel):
    dataset_id: int
    archived_at: datetime
    versions_archived: int
    versions_referenced_by_models: int
