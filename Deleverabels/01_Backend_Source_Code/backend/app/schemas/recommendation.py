"""Recommendation schemas. Rule-based guidance for human review; never AI conclusions about individuals."""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    InterventionPriority,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationScope,
    RecommendationStatus,
)
from app.schemas.common import PaginationParams, trimmed
from app.services.recommendation_rules import DISCLAIMER
from app.services.recommendation_rules_v2 import DISCLAIMER as DISCLAIMER_V2

Strategy = Literal["rules-v1", "rules-v2"]

PositiveId = Annotated[int, Field(gt=0)]


class RecommendationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: str
    rule_version: str
    scope: RecommendationScope
    category: RecommendationCategory
    priority: RecommendationPriority
    status: RecommendationStatus
    employee_id: int | None
    department_id: int | None
    risk_alert_id: int | None
    intervention_id: int | None
    title: str
    suggested_action: str
    explanation: str
    evidence: dict[str, Any]
    evidence_window: str
    source_data_as_of: datetime | None
    evaluated_at: datetime
    reviewed_by_user_id: int | None
    reviewed_at: datetime | None
    dismissal_reason: str | None
    completion_outcome: str | None
    version: int
    # rules-v2 fields (null for rules-v1 records)
    task_id: int | None = None
    rank_score: int | None = Field(default=None, description="Deterministic ordering score 0-100; not a probability")
    priority_reasons: list[str] | None = None
    superseded_by_id: int | None = None
    basis: str = "rule-based"
    disclaimer: str = DISCLAIMER

    @model_validator(mode="after")
    def _strategy_disclaimer(self) -> "RecommendationRead":
        if self.rule_version == "rules-v2":
            self.disclaimer = DISCLAIMER_V2
        return self


class ListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: RecommendationStatus | None = None
    scope: RecommendationScope | None = None
    category: RecommendationCategory | None = None
    priority: RecommendationPriority | None = None
    rule_id: Annotated[str, Field(pattern=r"^[A-Z_]{3,50}$")] | None = None
    employee_id: PositiveId | None = None
    department_id: PositiveId | None = None
    rule_version: Strategy | None = None
    sort: Literal["evaluated", "rank"] = Field(
        default="evaluated", description="evaluated: newest first (default). rank: rank_score, then priority, then newest; "
                                         "records without a rank_score (rules-v1) last. Ties by id.")


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: PositiveId | None = Field(default=None, description="Omit for organization-wide (ADMIN/HR only)")
    include_individual: bool = True
    max_alerts: Annotated[int, Field(ge=1, le=5000)] = Field(
        default=200, description="Per-source limit (alerts; for rules-v2 also overdue interventions and goals)")
    strategy: Strategy = Field(default="rules-v1", description="rules-v1 (default, unchanged) or rules-v2")


class SkippedRule(BaseModel):
    rule_id: str
    status: str
    reason: str
    subject: str


class RankedCandidate(BaseModel):
    rule_id: str
    subject: str
    priority: RecommendationPriority
    rank_score: int
    outcome: Literal["created", "already_recorded", "being_addressed"]
    recommendation_id: int | None


class EvaluateResult(BaseModel):
    rule_version: str
    created: int
    already_recorded: int
    alerts_examined: int
    aggregate_subjects_examined: int
    not_actionable: list[SkippedRule]
    recommendation_ids: list[int]
    # rules-v2 only (defaults keep the rules-v1 response unchanged)
    being_addressed: int = 0
    superseded: int = 0
    truncated_sources: list[str] = Field(default_factory=list)
    ranked: list[RankedCandidate] = Field(default_factory=list, description="rules-v2 candidates in rank order")
    note: str = DISCLAIMER


class VersionedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveId | None = None


class DismissRequest(VersionedAction):
    reason: trimmed(5, 500)


class CompleteRequest(VersionedAction):
    outcome: trimmed(5, 1000)


class CreateInterventionRequest(BaseModel):
    """Link an existing intervention (intervention_id) or create a new one from this recommendation."""

    model_config = ConfigDict(extra="forbid")

    intervention_id: PositiveId | None = None
    title: trimmed(3, 150) | None = None
    description: trimmed(1, 1000) | None = None
    priority: InterventionPriority | None = None
    due_date: date | None = None
    assigned_to_user_id: PositiveId | None = None


class LinkTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: PositiveId
    expected_version: PositiveId | None = None


class HistoryEvent(BaseModel):
    at: datetime
    action: str
    actor_user_id: int | None
    actor_hidden: bool
    details: dict[str, Any] | None


class RecommendationHistory(BaseModel):
    recommendation_id: int
    rule_id: str
    rule_version: str
    evaluated_at: datetime
    status: RecommendationStatus
    superseded_by_id: int | None
    events: list[HistoryEvent]


class Summary(BaseModel):
    by_status: dict[str, int]
    active_by_category: dict[str, int]
    active_by_priority: dict[str, int]
    active_by_scope: dict[str, int]
    note: str = DISCLAIMER
