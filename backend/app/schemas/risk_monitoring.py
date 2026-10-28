"""Risk-monitoring schemas. Alerts are HR review aids, not proof of intent or grounds for automatic decisions."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.core.config import get_settings
from app.models.base import utcnow
from app.models.enums import AlertPriority, AlertStatus, EscalationStage, RiskCategory, RiskEventType
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.common import PaginationParams, trimmed
from app.services import alert_sla

PositiveId = Annotated[int, Field(gt=0)]
DISCLAIMER = ("Risk alerts are review aids based on statistical estimates. They are not proof of an employee's "
              "intentions and must not be the basis for any automatic employment decision.")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: Annotated[int, Field(ge=1, le=5000)] = Field(default=200, description="Employees to examine (capped by RISK_MONITORING_MAX_BATCH)")
    after_employee_id: Annotated[int, Field(ge=0)] = Field(default=0, description="Cursor from a previous run's next_after_employee_id")


class RunSummary(BaseModel):
    examined_employees: int
    predictions_considered: int
    new_events: int
    alerts_created: int
    alerts_updated: int
    skipped: dict[str, int]
    stale_predictions: int
    next_after_employee_id: int | None
    active_model_available: bool
    note: str = DISCLAIMER


class SlaRead(BaseModel):
    """Review-deadline status computed at response time (never stored, never changes the alert)."""

    state: Literal["ON_TRACK", "APPROACHING", "OVERDUE", "ACKNOWLEDGED", "CLOSED"]
    review_window_hours: int
    review_due_at: datetime
    approaching_at: datetime
    met_deadline: bool | None = Field(description="For acknowledged/closed alerts: first review action by the deadline")


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee: AttendanceEmployeeRef
    trigger_type: RiskEventType
    priority: AlertPriority
    status: AlertStatus
    latest_prediction_id: int
    latest_score: Decimal
    latest_category: RiskCategory
    model_version: str
    event_count: int
    first_detected_at: datetime
    last_observed_at: datetime
    assigned_to_user_id: int | None
    acknowledged_at: datetime | None
    acknowledged_by_user_id: int | None
    resolved_at: datetime | None
    resolved_by_user_id: int | None
    resolution_reason: str | None
    reviewer_notes: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def sla(self) -> SlaRead:
        status = alert_sla.sla_status(self.priority, self.status, self.first_detected_at, self.acknowledged_at,
                                      self.resolved_at, utcnow(), alert_sla.SlaPolicy.from_settings(get_settings()))
        return SlaRead(state=status.state, review_window_hours=status.review_window_hours,
                       review_due_at=status.review_due_at, approaching_at=status.approaching_at,
                       met_deadline=status.met_deadline)


class AlertListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: AlertStatus | None = None
    priority: AlertPriority | None = None
    trigger_type: RiskEventType | None = None
    department_id: PositiveId | None = None
    employee_id: PositiveId | None = None
    assigned_to_me: bool = False
    detected_from: date | None = Field(default=None, description="First detected on or after (organization-local date)")
    detected_to: date | None = Field(default=None, description="First detected on or before (organization-local date)")

    @model_validator(mode="after")
    def _detected_range(self) -> "AlertListParams":
        if self.detected_from and self.detected_to and self.detected_from > self.detected_to:
            raise ValueError("detected_from must be on or before detected_to")
        return self


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: trimmed(1, 1000) | None = None


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["RESOLVED", "DISMISSED"]
    reason: trimmed(5, 500)
    note: trimmed(1, 1000) | None = None


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: PositiveId


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: RiskEventType
    current_prediction_id: int
    previous_prediction_id: int | None
    current_score: Decimal
    previous_score: Decimal | None
    current_category: RiskCategory
    previous_category: RiskCategory | None
    model_version: str
    detected_at: datetime
    alert_id: int | None


class HistoryParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    event_type: RiskEventType | None = None


class RiskSummary(BaseModel):
    alerts_by_status: dict[str, int]
    active_alerts_by_priority: dict[str, int]
    active_alerts_by_department: dict[str, int]
    alerts_created_last_7_days: int
    alerts_created_last_30_days: int
    events_last_30_days_by_type: dict[str, int]
    # Active alerts by review-deadline state (ON_TRACK / APPROACHING / OVERDUE / ACKNOWLEDGED).
    active_alerts_by_sla_state: dict[str, int] = Field(default_factory=dict)
    note: str = DISCLAIMER


class EscalationEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: Annotated[int, Field(ge=1, le=5000)] = Field(default=200, description="Alerts to examine (capped by ALERT_ESCALATION_MAX_BATCH)")
    after_alert_id: Annotated[int, Field(ge=0)] = Field(default=0, description="Cursor from a previous evaluation's next_after_alert_id")
    department_id: PositiveId | None = None


class EscalationRead(BaseModel):
    alert_id: int
    stage: EscalationStage
    priority: AlertPriority
    review_due_at: datetime
    recipient_count: int


class EscalationEvaluateResult(BaseModel):
    examined_alerts: int
    on_track: int
    approaching: int
    overdue: int
    acknowledged: int
    new_escalations: list[EscalationRead]
    already_escalated: int
    approaching_not_notified: int
    no_eligible_recipient_alert_ids: list[int]
    notifications_created: int
    next_after_alert_id: int | None
    note: str = ("Escalation only notifies ADMIN/HR reviewers. It never acknowledges, resolves or dismisses an alert, "
                 "and never creates interventions or recommendations. " + DISCLAIMER)
