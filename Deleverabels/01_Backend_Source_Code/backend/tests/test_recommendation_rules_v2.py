"""rules-v2: pure deterministic rules, ranking and fingerprints (no database). Synthetic inputs only."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import RecommendationScope as S
from app.services import recommendation_rules_v2 as v2

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 27)
LIMITS = v2.Limits(stability_below=60)
PUNITIVE = ("terminat", "dismiss the employee", "demot", "reduce pay", "disciplin", "fire ", "warning letter", "resign",
            "intend", "health", "family")


def alert(**overrides) -> v2.AlertSignal:
    base = dict(alert_id=7, employee_id=3, department_id=2, alert_priority="HIGH", sla_state="ON_TRACK",
                has_active_intervention=False, prediction_problem=None, last_observed_at=NOW - timedelta(days=2))
    return v2.AlertSignal(**{**base, **overrides})


def run(fn, *args) -> v2.RuleResult:
    result = v2.RuleResult()
    fn(*args, result)
    return result


def aggregate(**overrides) -> v2.AggregateSignal:
    base = dict(scope=S.DEPARTMENT, department_id=2, window="2026-10", stability_status="ok", stability_score=90.0,
                month_end_active=((1, 10), (2, 10), (3, 10), (4, 10)), latest_snapshot_at=NOW - timedelta(days=3),
                overdue_high_tasks=0, overdue_critical_tasks=0, oldest_task_overdue_days=None, overdue_reviews=0,
                oldest_review_overdue_days=None)
    return v2.AggregateSignal(**{**base, **overrides})


# --- Individual rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize("priority, sla, expected", [("LOW", "OVERDUE", "LOW"), ("MEDIUM", "OVERDUE", "MEDIUM"),
                                                     ("HIGH", "ON_TRACK", "HIGH"), ("HIGH", "OVERDUE", "CRITICAL")])
def test_alert_support_review_priority(priority, sla, expected) -> None:
    [candidate] = run(v2.alert_rules, alert(alert_priority=priority, sla_state=sla), NOW).candidates
    assert (candidate.rule_id, candidate.priority.value, candidate.category.value) == (
        "V2_ALERT_SUPPORT_REVIEW", expected, "SUPPORT_CHECK_IN")
    assert candidate.risk_alert_id == 7 and candidate.evidence_window == "alert:7"
    assert "not evidence of an intention" in candidate.explanation


@pytest.mark.parametrize("problem", ["missing", "incompatible", "stale"])
def test_unusable_prediction_suggests_refresh_not_employee_action(problem) -> None:
    result = run(v2.alert_rules, alert(prediction_problem=problem, sla_state="OVERDUE"), NOW)
    [candidate] = result.candidates
    assert (candidate.rule_id, candidate.category.value, candidate.priority.value) == ("V2_PREDICTION_REFRESH", "DATA_REFRESH", "MEDIUM")
    assert result.outcomes[0].status == "INSUFFICIENT_DATA"
    low = run(v2.alert_rules, alert(prediction_problem=problem, alert_priority="LOW"), NOW).candidates[0]
    assert low.priority.value == "LOW"  # missing data never raises a priority


def test_active_intervention_suppresses_support_review() -> None:
    result = run(v2.alert_rules, alert(has_active_intervention=True), NOW)
    assert result.candidates == [] and result.outcomes[0].status == "SUPPRESSED"


@pytest.mark.parametrize("days, priority, expected", [(0, "MEDIUM", None), (-3, "MEDIUM", None), (5, "MEDIUM", "MEDIUM"),
                                                      (14, "MEDIUM", "HIGH"), (5, "HIGH", "HIGH"), (20, "HIGH", "CRITICAL")])
def test_intervention_follow_up(days, priority, expected) -> None:
    signal = v2.InterventionSignal(11, 3, 2, priority, TODAY - timedelta(days=days), NOW - timedelta(days=1))
    candidates = run(v2.intervention_rules, signal, NOW, TODAY, LIMITS).candidates
    assert [c.priority.value for c in candidates] == ([expected] if expected else [])
    if expected:
        assert candidates[0].evidence["days_overdue"] == days and candidates[0].category.value == "FOLLOW_UP_REVIEW"


@pytest.mark.parametrize("at_risk, overdue, expected", [((), (), None), ((5,), (), "MEDIUM"), ((), (6,), "LOW"),
                                                        ((5,), (6, 8), "MEDIUM")])
def test_goal_check_in(at_risk, overdue, expected) -> None:
    signal = v2.GoalSignal(3, 2, at_risk, overdue, NOW - timedelta(days=10))
    candidates = run(v2.goal_rules, signal, NOW).candidates
    assert [c.priority.value for c in candidates] == ([expected] if expected else [])
    if expected:
        assert candidates[0].evidence["at_risk_goal_ids"] == list(at_risk)
        assert "not the employee's commitment" in candidates[0].explanation


# --- Aggregate rules ---------------------------------------------------------------------------------------


def test_stability_review_and_insufficient_data() -> None:
    def ids(**o):
        return [(c.rule_id, c.priority.value) for c in run(v2.aggregate_rules, aggregate(**o), NOW, LIMITS).candidates]

    assert ids() == []
    assert ids(stability_score=50.0) == [("V2_STABILITY_REVIEW", "MEDIUM")]
    assert ids(month_end_active=((1, 9), (2, 8), (3, 7), (4, 6))) == [("V2_STABILITY_REVIEW", "MEDIUM")]
    assert ids(stability_score=50.0, month_end_active=((1, 9), (2, 8), (3, 7), (4, 6))) == [("V2_STABILITY_REVIEW", "HIGH")]
    result = run(v2.aggregate_rules, aggregate(stability_status="insufficient_data", stability_score=None,
                                               month_end_active=((1, 9),)), NOW, LIMITS)
    assert result.candidates == [] and result.outcomes[0].status == "INSUFFICIENT_DATA"


def test_department_follow_up_rules_use_counts_only() -> None:
    result = run(v2.aggregate_rules, aggregate(overdue_high_tasks=3, overdue_critical_tasks=1, oldest_task_overdue_days=9,
                                               overdue_reviews=2, oldest_review_overdue_days=4), NOW, LIMITS)
    by_rule = {c.rule_id: c for c in result.candidates}
    assert by_rule["V2_TASK_FOLLOW_UP"].priority.value == "HIGH"
    assert by_rule["V2_TASK_FOLLOW_UP"].evidence["overdue_high_or_critical_tasks"] == 3
    assert by_rule["V2_REVIEW_FOLLOW_UP"].priority.value == "MEDIUM"
    assert all(c.employee_id is None and c.scope == S.DEPARTMENT for c in result.candidates)
    org = run(v2.aggregate_rules, aggregate(scope=S.ORGANIZATION, department_id=None, overdue_high_tasks=None,
                                            overdue_reviews=None), NOW, LIMITS)
    assert org.candidates == []


# --- Ranking, determinism, fingerprints ----------------------------------------------------------------------


def test_rank_score_formula_and_reasons() -> None:
    [critical] = run(v2.alert_rules, alert(sla_state="OVERDUE", last_observed_at=NOW - timedelta(days=2)), NOW).candidates
    assert critical.rank_score == 80 + 0 + 5 + 3  # base CRITICAL + fresh evidence + individual follow-up
    assert critical.priority_reasons == ("Linked risk alert priority HIGH",
                                         "Raised to CRITICAL: HIGH alert past its review deadline",
                                         "Base 80 for CRITICAL priority", "+5 evidence freshness (fresh, 2 days)",
                                         "+3 direct individual follow-up")
    signal = v2.InterventionSignal(11, 3, 2, "MEDIUM", TODAY - timedelta(days=20), NOW - timedelta(days=60))
    [late] = run(v2.intervention_rules, signal, NOW, TODAY, LIMITS).candidates
    assert late.rank_score == 60 + 6 + 0 + 3 and late.evidence["freshness"] == "old"
    unknown = run(v2.alert_rules, alert(last_observed_at=None, alert_priority="LOW"), NOW).candidates[0]
    assert unknown.rank_score == 20 + 3 and unknown.evidence["freshness"] == "unknown"


def test_ordering_is_stable_and_deterministic() -> None:
    def build() -> list[tuple]:
        result = v2.RuleResult()
        for alert_id, employee in ((9, 4), (7, 3), (8, 5)):
            v2.alert_rules(alert(alert_id=alert_id, employee_id=employee, alert_priority="MEDIUM"), NOW, result)
        v2.aggregate_rules(aggregate(overdue_high_tasks=1, overdue_critical_tasks=0, oldest_task_overdue_days=0), NOW,
                           LIMITS, result)
        return [(c.rule_id, c.subject, c.rank_score, c.priority.value, c.explanation, c.dedup_key) for c in result.ranked()]

    first, second = build(), build()
    assert first == second
    assert [row[1] for row in first] == ["employee:3", "employee:4", "employee:5", "department:2"]  # tie: by subject


def test_fingerprint_tracks_material_evidence_only() -> None:
    base = run(v2.alert_rules, alert(), NOW).candidates[0]
    later = run(v2.alert_rules, alert(last_observed_at=NOW - timedelta(days=20)), NOW + timedelta(days=1)).candidates[0]
    assert base.fingerprint == later.fingerprint and base.dedup_key == later.dedup_key  # age only
    changed = run(v2.alert_rules, alert(sla_state="OVERDUE"), NOW).candidates[0]
    assert changed.fingerprint != base.fingerprint and changed.lineage == base.lineage
    assert base.dedup_key.startswith("V2_ALERT_SUPPORT_REVIEW:rules-v2:employee:3:alert:7:")


def test_language_is_supportive_and_score_free() -> None:
    result = v2.RuleResult()
    v2.alert_rules(alert(sla_state="OVERDUE"), NOW, result)
    v2.alert_rules(alert(alert_id=8, employee_id=4, prediction_problem="stale"), NOW, result)
    v2.intervention_rules(v2.InterventionSignal(1, 3, 2, "HIGH", TODAY - timedelta(days=30), NOW), NOW, TODAY, LIMITS, result)
    v2.goal_rules(v2.GoalSignal(3, 2, (1,), (2,), NOW), NOW, result)
    v2.aggregate_rules(aggregate(stability_score=10.0, overdue_high_tasks=1, overdue_critical_tasks=1, overdue_reviews=1),
                       NOW, LIMITS, result)
    assert len(result.candidates) == 7
    for c in result.candidates:
        text = f"{c.title} {c.suggested_action} {c.explanation}".lower()
        assert not any(word in text for word in PUNITIVE), (c.rule_id, text)
        assert 0 <= c.rank_score <= 100
    assert v2.RULE_VERSION == "rules-v2" and "not a probability" in v2.DISCLAIMER
