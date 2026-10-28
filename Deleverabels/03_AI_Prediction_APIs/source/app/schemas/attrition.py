"""Attrition-risk schemas. Scores are model probability x 100: a relative risk estimate, not a certainty."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ModelVersionStatus, RiskCategory
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.common import PaginationParams
from ml.attrition.predictor import LIMITATIONS

PositiveId = Annotated[int, Field(gt=0)]
MAX_BATCH = 100


class FeatureSnapshot(BaseModel):
    """Exactly the attrition-features-v1 inputs used for a prediction (no identifiers)."""

    tenure_months: int = Field(ge=0, le=600)
    late_rate_90d: float | None = Field(default=None, ge=0, le=1)
    avg_overtime_minutes_90d: float | None = Field(default=None, ge=0, le=1440)
    recorded_absence_days_90d: int = Field(ge=0, le=90)
    attendance_days_recorded_90d: int = Field(ge=0, le=90)
    department: str | None = Field(default=None, max_length=50)


class Factor(BaseModel):
    feature: str
    direction: str = Field(description="'raises' or 'lowers' the estimate relative to the training data")
    weight: float = Field(description="Size of the contribution to the model's log-odds (unitless)")


class PredictionRead(BaseModel):
    id: int
    employee: AttendanceEmployeeRef
    risk_score: Decimal = Field(description="0-100; estimated probability x 100, not a confidence score")
    risk_category: RiskCategory
    model_version: str
    feature_schema_version: str
    predicted_at: datetime
    features: FeatureSnapshot
    factors: list[Factor] = Field(description="Strongest associations for this estimate; not causes")
    missing_inputs: list[str] = Field(description="Inputs with no data; imputed and excluded from factors")
    limitations: list[str] = list(LIMITATIONS)


class BatchPredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_ids: Annotated[list[PositiveId], Field(min_length=1, max_length=MAX_BATCH)]

    @field_validator("employee_ids")
    @classmethod
    def _unique(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("employee_ids must be unique")
        return value


class SkippedPrediction(BaseModel):
    employee_id: int
    reason: str


class BatchPredictResult(BaseModel):
    batch_id: str
    model_version: str
    predictions: list[PredictionRead]
    skipped: list[SkippedPrediction]


class HistoryParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")


class SummaryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: PositiveId | None = None


class RiskSummary(BaseModel):
    employees_with_predictions: int
    by_category: dict[str, int]
    model_versions: list[str]
    latest_prediction_at: datetime | None
    note: str = ("Latest prediction per active employee. Counts describe model estimates, not outcomes, "
                 "and must not drive individual employment decisions.")


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    model_version: str
    feature_schema_version: str
    status: ModelVersionStatus
    trained_at: datetime
    training_rows: int
    dataset_fingerprint: str
    evaluation: dict
    activated_at: datetime | None
    dataset_version_id: int | None


class ModelStatus(BaseModel):
    available: bool
    reason: str | None
    active_model: ModelVersionRead | None
    recent_models: list[ModelVersionRead]
    feature_schema_version: str
    feature_schema: list[dict]
    future_features: dict[str, str]
    risk_thresholds: dict[str, float]
    monitoring: dict
    limitations: list[str] = list(LIMITATIONS)


class TrainRequest(BaseModel):
    """Train from a registered dataset version. Paths and file names are not accepted, and training never
    activates the model (use POST /models/{model_version}/activate after review)."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: PositiveId
    version: PositiveId | None = Field(default=None, description="Defaults to the latest version")


class DatasetRef(BaseModel):
    dataset_id: int
    version: int
    sha256: str


class TrainResult(BaseModel):
    model: ModelVersionRead
    dataset: DatasetRef
    gate_failures: list[str]
    note: str = ("Evaluation is on a held-out split of the supplied dataset only. A passing model is a CANDIDATE "
                 "and is not used until an administrator activates it. Dataset validation and these metrics do "
                 "not establish model quality, fairness or production readiness; production_validated stays false.")
