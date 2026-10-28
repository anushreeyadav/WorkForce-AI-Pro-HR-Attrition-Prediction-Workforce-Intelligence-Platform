"""Deterministic recommendation rules (no database, no model calls). Rule set version: rules-v1.

This is RULE-BASED GUIDANCE, not a trained model and not a causal finding. Risk scores are estimates, not
evidence of intent; aggregate metrics describe groups, not individuals. Rules never suggest punitive action
and never use protected traits. Lateness and recorded-absence inputs are deliberately NOT mapped to
individual suggestions (they can proxy health or family circumstances); such cases get only the general
supportive check-in. Missing data never triggers a rule; it yields INSUFFICIENT_DATA.

Individual rules (subject: an employee with an active risk alert):
  IND_ALERT_CHECKIN       active alert with no active intervention linked to it -> supportive check-in
  IND_WORKLOAD_REVIEW     strongest "raises" factor of the alert's compatible, fresh prediction is
                          avg_overtime_minutes_90d -> workload review
  IND_CAREER_CONVERSATION same, factor tenure_months -> development conversation
Aggregate rules (subject: organization or a department):
  AGG_SNAPSHOT_FRESHNESS   no snapshot, or latest older than WORKFORCE_SNAPSHOT_MAX_AGE_DAYS (organization)
  AGG_UNKNOWN_DEPARTURES   inactive employees without a recorded departure date
  AGG_TURNOVER_REVIEW      annualized turnover (snapshot denominator) >= RECOMMENDATION_TURNOVER_REVIEW_THRESHOLD
  AGG_LOW_STABILITY        stability score (status ok) < RECOMMENDATION_STABILITY_REVIEW_BELOW
  AGG_HEADCOUNT_DECLINE    three consecutive declines across four consecutive observed month-ends
  AGG_ALERT_CLUSTER        active risk alerts in a department >= RECOMMENDATION_ALERT_CLUSTER_MIN
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.models import RecommendationCategory as C
from app.models import RecommendationPriority as P
from app.models import RecommendationScope as S

RULE_VERSION = "rules-v1"
DISCLAIMER = ("Rule-based guidance for human review. Not a trained model, not a causal finding and not evidence of "
              "anyone's intentions. Never use it for punitive or employment decisions.")
FACTOR_RULES = {
    "avg_overtime_minutes_90d": ("IND_WORKLOAD_REVIEW", C.WORKLOAD_REVIEW, "Review workload and working hours",
                                 "Review workload, deadlines and working hours with the employee and team, and agree "
                                 "practical adjustments.", "working-hours"),
    "tenure_months": ("IND_CAREER_CONVERSATION", C.CAREER_DEVELOPMENT, "Discuss development opportunities",
                      "Offer a conversation about goals, learning and internal growth opportunities.", "tenure"),
}


@dataclass(frozen=True)
class Candidate:
    rule_id: str
    scope: S
    category: C
    priority: P
    title: str
    suggested_action: str
    explanation: str
    evidence: dict
    evidence_window: str
    source_data_as_of: datetime | None
    employee_id: int | None = None
    department_id: int | None = None
    risk_alert_id: int | None = None

    @property
    def subject(self) -> str:
        return f"employee:{self.employee_id}" if self.scope == S.INDIVIDUAL else (
            f"department:{self.department_id}" if self.scope == S.DEPARTMENT else "organization")

    @property
    def dedup_key(self) -> str:
        return f"{self.rule_id}:{RULE_VERSION}:{self.subject}:{self.evidence_window}"


@dataclass(frozen=True)
class Outcome:
    """A rule that did not produce an actionable recommendation (not persisted)."""

    rule_id: str
    status: str  # "INSUFFICIENT_DATA" | "SUPPRESSED"
    reason: str
    subject: str


@dataclass
class RuleResult:
    candidates: list[Candidate] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)


# --- Individual ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertFacts:
    alert_id: int
    employee_id: int
    priority: str  # alert priority LOW/MEDIUM/HIGH
    last_observed_at: datetime
    has_active_intervention: bool
    prediction_id: int | None
    prediction_compatible: bool
    prediction_fresh: bool
    predicted_at: datetime | None
    factors: list[dict]  # stored explanation factors (feature/direction/weight)
    missing_inputs: list[str]


def individual_rules(facts: AlertFacts) -> RuleResult:
    result = RuleResult()
    subject = f"employee:{facts.employee_id}"
    window = f"alert:{facts.alert_id}"
    priority = P(facts.priority)
    base_evidence = {"risk_alert_id": facts.alert_id, "alert_priority": facts.priority}

    if facts.has_active_intervention:
        result.outcomes.append(Outcome("IND_ALERT_CHECKIN", "SUPPRESSED", "An active intervention is already linked to this alert", subject))
    else:
        result.candidates.append(Candidate(
            "IND_ALERT_CHECKIN", S.INDIVIDUAL, C.SUPPORT_CHECK_IN, priority, "Offer a supportive check-in",
            "Arrange a supportive one-to-one conversation about how the employee is doing and what support would help.",
            f"Risk alert #{facts.alert_id} ({facts.priority} priority) is active and has no active intervention. The "
            "underlying risk score is a statistical estimate, not evidence of an intention to leave.",
            base_evidence, window, facts.last_observed_at, employee_id=facts.employee_id, risk_alert_id=facts.alert_id))

    if facts.prediction_id is None or not facts.prediction_compatible:
        result.outcomes.append(Outcome("IND_FACTOR_RULES", "INSUFFICIENT_DATA", "No compatible stored prediction for this alert", subject))
        return result
    if not facts.prediction_fresh:
        result.outcomes.append(Outcome("IND_FACTOR_RULES", "INSUFFICIENT_DATA", "The stored prediction is stale; refresh it first", subject))
        return result
    raising = [f for f in facts.factors if f.get("direction") == "raises" and f.get("feature") not in facts.missing_inputs]
    top = max(raising, key=lambda f: (f.get("weight", 0), f.get("feature", "")), default=None)
    if top is None or top["feature"] not in FACTOR_RULES:
        result.outcomes.append(Outcome("IND_FACTOR_RULES", "SUPPRESSED", "No job-related factor rule applies", subject))
        return result
    rule_id, category, title, action, label = FACTOR_RULES[top["feature"]]
    result.candidates.append(Candidate(
        rule_id, S.INDIVIDUAL, category, P.MEDIUM, title, action,
        f"For risk alert #{facts.alert_id}, {label} inputs were the strongest association with the model's estimate. "
        "This is an association in historical data, not a cause, and says nothing certain about this person.",
        {**base_evidence, "prediction_id": facts.prediction_id, "strongest_association": label},
        window, facts.predicted_at, employee_id=facts.employee_id, risk_alert_id=facts.alert_id))
    return result


# --- Aggregate -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregateFacts:
    scope: S  # ORGANIZATION or DEPARTMENT
    department_id: int | None
    window: str  # e.g. "2026-10" (evaluation month)
    latest_snapshot_at: datetime | None
    latest_snapshot_age_days: int | None
    inactive_without_recorded_departure: int
    annualized_turnover: float | None
    stability_status: str
    stability_score: float | None
    month_end_active: list[tuple[int, int]]  # (month_index, active) for observed months, sorted
    active_alerts: int | None  # None when not applicable (organization scope)


@dataclass(frozen=True)
class Thresholds:
    turnover: float
    stability_below: float
    alert_cluster_min: int
    snapshot_max_age_days: int


def _aggregate(facts: AggregateFacts, rule_id: str, category: C, priority: P, title: str, action: str,
               explanation: str, evidence: dict) -> Candidate:
    return Candidate(rule_id, facts.scope, category, priority, title, action, explanation, evidence, facts.window,
                     facts.latest_snapshot_at, department_id=facts.department_id)


def consecutive_declines(points: list[tuple[int, int]]) -> bool:
    """True when the last four observed month-ends are consecutive months and each is lower than the previous."""
    if len(points) < 4:
        return False
    last = points[-4:]
    return all(last[i + 1][0] == last[i][0] + 1 and last[i + 1][1] < last[i][1] for i in range(3))


def aggregate_rules(facts: AggregateFacts, limits: Thresholds) -> RuleResult:
    result = RuleResult()
    subject = "organization" if facts.scope == S.ORGANIZATION else f"department:{facts.department_id}"
    where = "the organization" if facts.scope == S.ORGANIZATION else f"department {facts.department_id}"

    if facts.scope == S.ORGANIZATION:
        stale = facts.latest_snapshot_age_days is None or facts.latest_snapshot_age_days > limits.snapshot_max_age_days
        if stale:
            age = "none have been captured" if facts.latest_snapshot_age_days is None else f"the latest is {facts.latest_snapshot_age_days} days old"
            result.candidates.append(_aggregate(
                facts, "AGG_SNAPSHOT_FRESHNESS", C.DATA_QUALITY, P.MEDIUM, "Capture a workforce snapshot",
                "Capture a workforce snapshot now and schedule regular monthly captures.",
                f"Workforce snapshots provide the only historical headcount, and {age}. Trend, turnover and stability "
                "analytics stay unavailable without them.", {"latest_snapshot_age_days": facts.latest_snapshot_age_days}))

    if facts.inactive_without_recorded_departure > 0:
        result.candidates.append(_aggregate(
            facts, "AGG_UNKNOWN_DEPARTURES", C.DATA_QUALITY, P.LOW, "Review leaver records",
            "Check inactive employee records whose departure was not recorded through the status workflow.",
            f"{facts.inactive_without_recorded_departure} inactive employee(s) in {where} have no recorded departure "
            "date, so they are excluded from turnover figures.",
            {"inactive_without_recorded_departure": facts.inactive_without_recorded_departure}))

    if facts.annualized_turnover is None:
        result.outcomes.append(Outcome("AGG_TURNOVER_REVIEW", "INSUFFICIENT_DATA", "No snapshot-based turnover denominator", subject))
    elif facts.annualized_turnover >= limits.turnover:
        result.candidates.append(_aggregate(
            facts, "AGG_TURNOVER_REVIEW", C.DEPARTMENT_RETENTION_REVIEW, P.HIGH, "Hold a retention review",
            "Review recent departures, exit feedback and team conditions with the relevant leaders.",
            f"Annualized recorded turnover in {where} is {facts.annualized_turnover:.1%}, at or above the "
            f"{limits.turnover:.0%} review threshold (recorded departures over average snapshot headcount).",
            {"annualized_turnover": facts.annualized_turnover, "threshold": limits.turnover}))

    if facts.stability_status != "ok" or facts.stability_score is None:
        result.outcomes.append(Outcome("AGG_LOW_STABILITY", "INSUFFICIENT_DATA", f"Stability indicator status: {facts.stability_status}", subject))
    elif facts.stability_score < limits.stability_below:
        result.candidates.append(_aggregate(
            facts, "AGG_LOW_STABILITY", C.WORKFORCE_TREND_REVIEW, P.MEDIUM, "Review workforce stability",
            "Review headcount changes, turnover and staffing plans for the period.",
            f"The workforce stability indicator for {where} is {facts.stability_score} (below {limits.stability_below}). "
            "It is a documented aggregate heuristic, not a measure of any individual.",
            {"stability_score": facts.stability_score, "threshold": limits.stability_below}))

    if len(facts.month_end_active) < 4:
        result.outcomes.append(Outcome("AGG_HEADCOUNT_DECLINE", "INSUFFICIENT_DATA", "Fewer than four observed month-ends", subject))
    elif consecutive_declines(facts.month_end_active):
        values = [v for _, v in facts.month_end_active[-4:]]
        result.candidates.append(_aggregate(
            facts, "AGG_HEADCOUNT_DECLINE", C.WORKFORCE_TREND_REVIEW, P.MEDIUM, "Review declining headcount",
            "Review workload distribution and hiring plans given the sustained headcount decline.",
            f"Month-end active headcount in {where} declined three months in a row ({' → '.join(map(str, values))}).",
            {"month_end_active_headcount": values}))

    if facts.active_alerts is not None and facts.active_alerts >= limits.alert_cluster_min:
        result.candidates.append(_aggregate(
            facts, "AGG_ALERT_CLUSTER", C.DEPARTMENT_RETENTION_REVIEW, P.MEDIUM, "Review team conditions",
            "Hold a team-level review of workload and working conditions (not a review of individuals).",
            f"{facts.active_alerts} active risk alerts exist in {where} (threshold {limits.alert_cluster_min}). Alerts are "
            "estimates and are not confirmed departures.", {"active_risk_alerts": facts.active_alerts}))
    return result
