"""Deterministic recommendation rules, version rules-v2 (no database, no model calls, no external services).

RULE-BASED GUIDANCE for human review, not a trained or validated predictor and not a causal finding. Inputs are
existing records only. Rules never infer intent, health, family circumstances or protected characteristics;
attendance and leave patterns are deliberately not used (they can proxy those). Missing or stale data never
raises a priority: it yields INSUFFICIENT_DATA, or a data-refresh suggestion instead of an employee action.

Rules
  Individual (subject: an employee in the caller's scope, never the caller)
    V2_ALERT_SUPPORT_REVIEW    active risk alert, no active intervention, usable prediction -> HR reviews whether a
                               supportive conversation is appropriate. Priority = alert priority; HIGH alert whose
                               review deadline is OVERDUE -> CRITICAL.
    V2_PREDICTION_REFRESH      active risk alert whose stored prediction is missing, incompatible or stale -> refresh
                               the analysis first (no employee-specific action). LOW; MEDIUM for a HIGH alert.
    V2_INTERVENTION_FOLLOW_UP  active intervention past its due date -> review the plan. MEDIUM; one level up when
                               >= 14 days overdue and one level up for a HIGH-priority intervention (max CRITICAL).
    V2_GOAL_CHECK_IN           goals AT_RISK, or active and past their target date -> manager check-in about support.
                               MEDIUM when any goal is AT_RISK, otherwise LOW.
  Aggregate (subject: organization or a department; counts only, never individual records)
    V2_STABILITY_REVIEW        stability indicator below RECOMMENDATION_STABILITY_REVIEW_BELOW and/or three consecutive
                               month-end headcount declines -> investigate trends. MEDIUM; HIGH when both.
    V2_TASK_FOLLOW_UP          unresolved HIGH/CRITICAL tasks past their due date (department) -> review blockers.
                               MEDIUM; HIGH when any is CRITICAL.
    V2_REVIEW_FOLLOW_UP        performance reviews not yet submitted by the reviewer after the cycle's manager deadline
                               (department) -> follow up on the review process. MEDIUM.

Ranking (rank_score 0-100, a deterministic ORDERING score, not a probability or confidence):
    base by priority LOW 20 / MEDIUM 40 / HIGH 60 / CRITICAL 80
    + urgency: min(10, days overdue // 3) where a due date or deadline is overdue
    + freshness: +5 if the newest supporting record is <= 7 days old, +2 if <= 30 days, else 0
    + direct individual follow-up: +3 for individual-scope items
  Ties: priority, rule_id, subject, evidence window (stable).
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime

from app.models import RecommendationCategory as C
from app.models import RecommendationPriority as P
from app.models import RecommendationScope as S

RULE_VERSION = "rules-v2"
DISCLAIMER = ("Rule-based guidance (rules-v2) for human review. Not a trained or validated model, not a causal finding "
              "and not evidence of anyone's intentions. rank_score orders suggestions; it is not a probability. Never "
              "use it for punitive or employment decisions.")
ORDER = [P.LOW, P.MEDIUM, P.HIGH, P.CRITICAL]
BASE = {P.LOW: 20, P.MEDIUM: 40, P.HIGH: 60, P.CRITICAL: 80}


def raise_level(priority: P, steps: int = 1) -> P:
    return ORDER[min(ORDER.index(priority) + steps, len(ORDER) - 1)]


# --- Signals (normalized inputs) ----------------------------------------------------------------------


@dataclass(frozen=True)
class AlertSignal:
    alert_id: int
    employee_id: int
    department_id: int
    alert_priority: str  # LOW / MEDIUM / HIGH
    sla_state: str  # ON_TRACK / APPROACHING / OVERDUE / ACKNOWLEDGED
    has_active_intervention: bool
    prediction_problem: str | None  # None = usable; "missing" / "incompatible" / "stale"
    last_observed_at: datetime


@dataclass(frozen=True)
class InterventionSignal:
    intervention_id: int
    employee_id: int
    department_id: int
    priority: str
    due_date: date
    updated_at: datetime


@dataclass(frozen=True)
class GoalSignal:
    employee_id: int
    department_id: int
    at_risk_goal_ids: tuple[int, ...]
    overdue_goal_ids: tuple[int, ...]
    latest_update: datetime


@dataclass(frozen=True)
class AggregateSignal:
    scope: S
    department_id: int | None
    window: str  # evaluation month "YYYY-MM"
    stability_status: str
    stability_score: float | None
    month_end_active: tuple[tuple[int, int], ...]
    latest_snapshot_at: datetime | None
    overdue_high_tasks: int | None  # None for organization scope
    overdue_critical_tasks: int | None
    oldest_task_overdue_days: int | None
    overdue_reviews: int | None
    oldest_review_overdue_days: int | None


@dataclass(frozen=True)
class Limits:
    stability_below: float
    follow_up_escalation_days: int = 14


# --- Output --------------------------------------------------------------------------------------------


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
    rank_score: int
    priority_reasons: tuple[str, ...]
    employee_id: int | None = None
    department_id: int | None = None
    risk_alert_id: int | None = None

    @property
    def subject(self) -> str:
        return f"employee:{self.employee_id}" if self.scope == S.INDIVIDUAL else (
            f"department:{self.department_id}" if self.scope == S.DEPARTMENT else "organization")

    @property
    def fingerprint(self) -> str:
        """Hash of the material inputs (rule, subject, window, priority, evidence). Timestamps are excluded."""
        material = {k: v for k, v in self.evidence.items() if k not in ("evidence_age_days", "freshness")}
        canonical = json.dumps({"rule": self.rule_id, "subject": self.subject, "window": self.evidence_window,
                                "priority": self.priority.value, "evidence": material}, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def dedup_key(self) -> str:
        return f"{self.rule_id}:{RULE_VERSION}:{self.subject}:{self.evidence_window}:{self.fingerprint[:16]}"

    @property
    def lineage(self) -> tuple[str, str, str]:
        """Same rule, subject and window = the same recommendation over time (a new fingerprint supersedes)."""
        return self.rule_id, self.subject, self.evidence_window


@dataclass(frozen=True)
class Outcome:
    rule_id: str
    status: str  # INSUFFICIENT_DATA / SUPPRESSED
    reason: str
    subject: str


@dataclass
class RuleResult:
    candidates: list[Candidate] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)

    def ranked(self) -> list[Candidate]:
        return sorted(self.candidates, key=lambda c: (-c.rank_score, -ORDER.index(c.priority), c.rule_id, c.subject,
                                                      c.evidence_window))


# --- Ranking ----------------------------------------------------------------------------------------------


def _freshness(newest: datetime | None, now: datetime) -> tuple[int | None, str, int]:
    if newest is None:
        return None, "unknown", 0
    age = max((now - newest).days, 0)
    if age <= 7:
        return age, "fresh", 5
    if age <= 30:
        return age, "aging", 2
    return age, "old", 0


def _finish(rule_id: str, scope: S, category: C, priority: P, title: str, action: str, explanation: str, evidence: dict,
            window: str, newest: datetime | None, now: datetime, reasons: list[str], overdue_days: int = 0,
            **subject) -> Candidate:
    age, label, fresh_points = _freshness(newest, now)
    urgency = min(10, max(overdue_days, 0) // 3)
    individual = 3 if scope == S.INDIVIDUAL else 0
    score = min(100, BASE[priority] + urgency + fresh_points + individual)
    reasons = [*reasons, f"Base {BASE[priority]} for {priority.value} priority"]
    if urgency:
        reasons.append(f"+{urgency} urgency ({overdue_days} days overdue)")
    if fresh_points:
        reasons.append(f"+{fresh_points} evidence freshness ({label}, {age} days)")
    if individual:
        reasons.append("+3 direct individual follow-up")
    return Candidate(rule_id, scope, category, priority, title, action, explanation,
                     {**evidence, "evidence_age_days": age, "freshness": label}, window, newest, score, tuple(reasons),
                     **subject)


# --- Rules --------------------------------------------------------------------------------------------------


def alert_rules(signal: AlertSignal, now: datetime, result: RuleResult) -> None:
    subject = f"employee:{signal.employee_id}"
    window = f"alert:{signal.alert_id}"
    alert_priority = P(signal.alert_priority)
    evidence = {"risk_alert_id": signal.alert_id, "alert_priority": signal.alert_priority, "review_sla": signal.sla_state}
    if signal.prediction_problem is not None:
        priority = P.MEDIUM if alert_priority == P.HIGH else P.LOW
        result.candidates.append(_finish(
            "V2_PREDICTION_REFRESH", S.INDIVIDUAL, C.DATA_REFRESH, priority, "Refresh the risk analysis first",
            "Refresh the stored prediction for this alert before considering any employee-specific action.",
            f"Risk alert #{signal.alert_id} relies on a prediction that is {signal.prediction_problem}, so no "
            "employee-specific suggestion is made until the analysis is refreshed.",
            {**evidence, "prediction": signal.prediction_problem}, window, signal.last_observed_at, now,
            [f"{'MEDIUM' if priority == P.MEDIUM else 'LOW'}: data refresh for a {signal.alert_priority} alert"],
            employee_id=signal.employee_id, department_id=signal.department_id, risk_alert_id=signal.alert_id))
        result.outcomes.append(Outcome("V2_ALERT_SUPPORT_REVIEW", "INSUFFICIENT_DATA",
                                       f"Prediction is {signal.prediction_problem}; refresh it first", subject))
        return
    if signal.has_active_intervention:
        result.outcomes.append(Outcome("V2_ALERT_SUPPORT_REVIEW", "SUPPRESSED",
                                       "An active intervention already addresses this alert", subject))
        return
    priority, reasons = alert_priority, [f"Linked risk alert priority {signal.alert_priority}"]
    if alert_priority == P.HIGH and signal.sla_state == "OVERDUE":
        priority = P.CRITICAL
        reasons.append("Raised to CRITICAL: HIGH alert past its review deadline")
    result.candidates.append(_finish(
        "V2_ALERT_SUPPORT_REVIEW", S.INDIVIDUAL, C.SUPPORT_CHECK_IN, priority,
        "Review whether a supportive conversation is appropriate",
        "HR and the manager review the situation and decide whether a supportive conversation is appropriate.",
        f"Risk alert #{signal.alert_id} ({signal.alert_priority} priority, review {signal.sla_state.lower()}) is active "
        "and no intervention addresses it. The underlying score is a statistical estimate, not evidence of an "
        "intention to leave.", evidence, window, signal.last_observed_at, now, reasons,
        employee_id=signal.employee_id, department_id=signal.department_id, risk_alert_id=signal.alert_id))


def intervention_rules(signal: InterventionSignal, now: datetime, today: date, limits: Limits, result: RuleResult) -> None:
    overdue = (today - signal.due_date).days
    if overdue <= 0:
        return
    priority, reasons = P.MEDIUM, ["MEDIUM: intervention follow-up overdue"]
    if overdue >= limits.follow_up_escalation_days:
        priority = raise_level(priority)
        reasons.append(f"Raised one level: {overdue} days overdue (>= {limits.follow_up_escalation_days})")
    if signal.priority == "HIGH":
        priority = raise_level(priority)
        reasons.append("Raised one level: HIGH-priority intervention")
    result.candidates.append(_finish(
        "V2_INTERVENTION_FOLLOW_UP", S.INDIVIDUAL, C.FOLLOW_UP_REVIEW, priority, "Review the intervention plan",
        "Check with the assignee whether the intervention is still appropriate, then update, reschedule or close it.",
        f"Intervention #{signal.intervention_id} was due on {signal.due_date.isoformat()} and is still open "
        f"({overdue} days overdue).",
        {"intervention_id": signal.intervention_id, "due_date": signal.due_date.isoformat(), "days_overdue": overdue},
        f"intervention:{signal.intervention_id}", signal.updated_at, now, reasons, overdue_days=overdue,
        employee_id=signal.employee_id, department_id=signal.department_id))


def goal_rules(signal: GoalSignal, now: datetime, result: RuleResult) -> None:
    if not signal.at_risk_goal_ids and not signal.overdue_goal_ids:
        return
    priority = P.MEDIUM if signal.at_risk_goal_ids else P.LOW
    reasons = ["MEDIUM: goal marked AT_RISK" if signal.at_risk_goal_ids else "LOW: goal past its target date"]
    result.candidates.append(_finish(
        "V2_GOAL_CHECK_IN", S.INDIVIDUAL, C.GOAL_SUPPORT, priority, "Check in about goal support",
        "The manager checks in about blockers and the support or adjustments the employee needs for these goals.",
        f"{len(signal.at_risk_goal_ids)} goal(s) are marked AT_RISK and {len(signal.overdue_goal_ids)} active goal(s) "
        "are past their target date. Goal status reflects progress, not the employee's commitment.",
        {"at_risk_goal_ids": list(signal.at_risk_goal_ids), "overdue_goal_ids": list(signal.overdue_goal_ids)},
        f"goals:{signal.employee_id}", signal.latest_update, now, reasons, employee_id=signal.employee_id,
        department_id=signal.department_id))


def consecutive_declines(points: tuple[tuple[int, int], ...]) -> bool:
    if len(points) < 4:
        return False
    last = points[-4:]
    return all(last[i + 1][0] == last[i][0] + 1 and last[i + 1][1] < last[i][1] for i in range(3))


def aggregate_rules(signal: AggregateSignal, now: datetime, limits: Limits, result: RuleResult) -> None:
    subject = "organization" if signal.scope == S.ORGANIZATION else f"department:{signal.department_id}"
    where = "the organization" if signal.scope == S.ORGANIZATION else f"department {signal.department_id}"
    place = {"department_id": signal.department_id}
    low = signal.stability_status == "ok" and signal.stability_score is not None and signal.stability_score < limits.stability_below
    declining = consecutive_declines(signal.month_end_active)
    if signal.stability_status != "ok" and len(signal.month_end_active) < 4:
        result.outcomes.append(Outcome("V2_STABILITY_REVIEW", "INSUFFICIENT_DATA",
                                       f"Stability indicator {signal.stability_status} and fewer than four month-ends", subject))
    elif low or declining:
        priority = P.HIGH if low and declining else P.MEDIUM
        reasons = ["HIGH: low stability and declining headcount" if priority == P.HIGH else
                   "MEDIUM: " + ("low stability indicator" if low else "three consecutive headcount declines")]
        evidence = {"stability_score": signal.stability_score if low else None, "threshold": limits.stability_below,
                    "month_end_active_headcount": [v for _, v in signal.month_end_active[-4:]] if declining else None}
        result.candidates.append(_finish(
            "V2_STABILITY_REVIEW", signal.scope, C.WORKFORCE_TREND_REVIEW, priority, "Investigate workforce trends",
            "Review aggregate headcount changes, turnover and staffing plans with the relevant leaders.",
            f"In {where}: " + "; ".join(filter(None, [
                f"the stability indicator is {signal.stability_score} (below {limits.stability_below})" if low else None,
                "month-end headcount declined three months in a row" if declining else None])) +
            ". These are aggregate indicators, not measures of any individual.",
            evidence, signal.window, signal.latest_snapshot_at, now, reasons, **place))

    if signal.scope != S.DEPARTMENT:
        return
    if signal.overdue_high_tasks:
        priority = P.HIGH if signal.overdue_critical_tasks else P.MEDIUM
        result.candidates.append(_finish(
            "V2_TASK_FOLLOW_UP", S.DEPARTMENT, C.FOLLOW_UP_REVIEW, priority, "Review overdue high-priority tasks",
            "Review blockers, ownership and due dates of the overdue high-priority tasks with the team.",
            f"{signal.overdue_high_tasks} unresolved HIGH or CRITICAL task(s) in {where} are past their due date "
            f"({signal.overdue_critical_tasks} CRITICAL).",
            {"overdue_high_or_critical_tasks": signal.overdue_high_tasks, "overdue_critical_tasks": signal.overdue_critical_tasks},
            signal.window, now, now,
            ["HIGH: includes overdue CRITICAL tasks" if priority == P.HIGH else "MEDIUM: overdue HIGH tasks"],
            overdue_days=signal.oldest_task_overdue_days or 0, **place))
    if signal.overdue_reviews:
        result.candidates.append(_finish(
            "V2_REVIEW_FOLLOW_UP", S.DEPARTMENT, C.FOLLOW_UP_REVIEW, P.MEDIUM, "Follow up on performance reviews",
            "Check with reviewers why their evaluations are late, and extend the deadline or reassign where needed.",
            f"{signal.overdue_reviews} performance review(s) in {where} still await the reviewer's evaluation after the "
            "cycle's manager-review deadline.", {"overdue_reviews": signal.overdue_reviews}, signal.window, now, now,
            ["MEDIUM: reviews past the manager deadline"], overdue_days=signal.oldest_review_overdue_days or 0, **place))
