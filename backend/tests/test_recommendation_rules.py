"""Deterministic recommendation rules (no database). All inputs are synthetic."""

from datetime import UTC, datetime

import pytest

from app.models import RecommendationCategory as C
from app.models import RecommendationScope as S
from app.services import recommendation_rules as rules

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
LIMITS = rules.Thresholds(turnover=0.20, stability_below=60, alert_cluster_min=3, snapshot_max_age_days=45)
PUNITIVE = ("terminat", "dismiss the employee", "demot", "reduce pay", "disciplin", "fire ", "warning letter", "deny")


def alert(**overrides) -> rules.AlertFacts:
    base = dict(alert_id=7, employee_id=3, priority="HIGH", last_observed_at=NOW, has_active_intervention=False,
                prediction_id=11, prediction_compatible=True, prediction_fresh=True, predicted_at=NOW,
                factors=[{"feature": "avg_overtime_minutes_90d", "direction": "raises", "weight": 1.2},
                         {"feature": "tenure_months", "direction": "raises", "weight": 0.4}],
                missing_inputs=[])
    return rules.AlertFacts(**{**base, **overrides})


def aggregate(**overrides) -> rules.AggregateFacts:
    base = dict(scope=S.DEPARTMENT, department_id=4, window="2026-10", latest_snapshot_at=NOW, latest_snapshot_age_days=5,
                inactive_without_recorded_departure=0, annualized_turnover=0.05, stability_status="ok",
                stability_score=90.0, month_end_active=[(1, 10), (2, 10), (3, 10), (4, 10)], active_alerts=0)
    return rules.AggregateFacts(**{**base, **overrides})


def ids(result: rules.RuleResult) -> list[str]:
    return [c.rule_id for c in result.candidates]


# --- Individual ---------------------------------------------------------------------------------


def test_checkin_and_workload_from_strongest_job_related_factor() -> None:
    result = rules.individual_rules(alert())
    assert ids(result) == ["IND_ALERT_CHECKIN", "IND_WORKLOAD_REVIEW"]
    checkin, workload = result.candidates
    assert (checkin.category, checkin.priority.value, checkin.risk_alert_id) == (C.SUPPORT_CHECK_IN, "HIGH", 7)
    assert workload.category == C.WORKLOAD_REVIEW and workload.evidence["prediction_id"] == 11
    assert "not a cause" in workload.explanation and "1.2" not in workload.explanation  # no weights or values
    assert checkin.dedup_key == "IND_ALERT_CHECKIN:rules-v1:employee:3:alert:7"


def test_tenure_factor_suggests_career_conversation() -> None:
    result = rules.individual_rules(alert(factors=[{"feature": "tenure_months", "direction": "raises", "weight": 2.0}]))
    assert ids(result) == ["IND_ALERT_CHECKIN", "IND_CAREER_CONVERSATION"]


@pytest.mark.parametrize("feature", ["late_rate_90d", "recorded_absence_days_90d", "department"])
def test_sensitive_proxies_and_unmapped_factors_get_no_specific_suggestion(feature) -> None:
    result = rules.individual_rules(alert(factors=[{"feature": feature, "direction": "raises", "weight": 3.0}]))
    assert ids(result) == ["IND_ALERT_CHECKIN"]
    assert [(o.rule_id, o.status) for o in result.outcomes] == [("IND_FACTOR_RULES", "SUPPRESSED")]


def test_lowering_and_missing_factors_are_ignored() -> None:
    result = rules.individual_rules(alert(
        factors=[{"feature": "avg_overtime_minutes_90d", "direction": "lowers", "weight": 5.0},
                 {"feature": "tenure_months", "direction": "raises", "weight": 0.1}],
    ))
    assert ids(result) == ["IND_ALERT_CHECKIN", "IND_CAREER_CONVERSATION"]
    missing = rules.individual_rules(alert(factors=[{"feature": "avg_overtime_minutes_90d", "direction": "raises", "weight": 1}],
                                           missing_inputs=["avg_overtime_minutes_90d"]))
    assert ids(missing) == ["IND_ALERT_CHECKIN"]


@pytest.mark.parametrize("overrides, reason", [
    ({"prediction_compatible": False}, "No compatible stored prediction"),
    ({"prediction_id": None}, "No compatible stored prediction"),
    ({"prediction_fresh": False}, "stale"),
])
def test_incompatible_or_stale_predictions_are_insufficient(overrides, reason) -> None:
    result = rules.individual_rules(alert(**overrides))
    assert ids(result) == ["IND_ALERT_CHECKIN"]
    assert result.outcomes[0].status == "INSUFFICIENT_DATA" and reason in result.outcomes[0].reason


def test_existing_intervention_suppresses_checkin() -> None:
    result = rules.individual_rules(alert(has_active_intervention=True))
    assert "IND_ALERT_CHECKIN" not in ids(result)
    assert ("IND_ALERT_CHECKIN", "SUPPRESSED") in [(o.rule_id, o.status) for o in result.outcomes]


# --- Aggregate -------------------------------------------------------------------------------------


def test_healthy_department_produces_nothing() -> None:
    result = rules.aggregate_rules(aggregate(), LIMITS)
    assert result.candidates == [] and result.outcomes == []


def test_each_aggregate_rule() -> None:
    result = rules.aggregate_rules(aggregate(
        inactive_without_recorded_departure=2, annualized_turnover=0.25, stability_score=50.0,
        month_end_active=[(1, 20), (2, 18), (3, 16), (4, 15)], active_alerts=3), LIMITS)
    assert ids(result) == ["AGG_UNKNOWN_DEPARTURES", "AGG_TURNOVER_REVIEW", "AGG_LOW_STABILITY",
                           "AGG_HEADCOUNT_DECLINE", "AGG_ALERT_CLUSTER"]
    turnover = result.candidates[1]
    assert turnover.priority.value == "HIGH" and "25.0%" in turnover.explanation
    assert result.candidates[3].evidence == {"month_end_active_headcount": [20, 18, 16, 15]}
    assert all(c.dedup_key.endswith(":department:4:2026-10") for c in result.candidates)


def test_missing_metrics_are_not_triggers() -> None:
    result = rules.aggregate_rules(aggregate(annualized_turnover=None, stability_status="insufficient_data",
                                             stability_score=None, month_end_active=[(1, 10)]), LIMITS)
    assert result.candidates == []
    assert {o.rule_id for o in result.outcomes} == {"AGG_TURNOVER_REVIEW", "AGG_LOW_STABILITY", "AGG_HEADCOUNT_DECLINE"}
    assert all(o.status == "INSUFFICIENT_DATA" for o in result.outcomes)


def test_decline_requires_consecutive_observed_months() -> None:
    assert rules.consecutive_declines([(1, 20), (2, 18), (3, 16), (4, 15)])
    assert not rules.consecutive_declines([(1, 20), (2, 18), (4, 16), (5, 15)])  # gap in month 3
    assert not rules.consecutive_declines([(1, 20), (2, 18), (3, 18), (4, 15)])  # a flat month


def test_snapshot_freshness_is_organization_level() -> None:
    org = aggregate(scope=S.ORGANIZATION, department_id=None, latest_snapshot_age_days=None, latest_snapshot_at=None,
                    active_alerts=None)
    assert ids(rules.aggregate_rules(org, LIMITS)) == ["AGG_SNAPSHOT_FRESHNESS"]
    stale = aggregate(scope=S.ORGANIZATION, department_id=None, latest_snapshot_age_days=60, active_alerts=None)
    assert ids(rules.aggregate_rules(stale, LIMITS)) == ["AGG_SNAPSHOT_FRESHNESS"]
    assert ids(rules.aggregate_rules(aggregate(latest_snapshot_age_days=None), LIMITS)) == []  # not for departments


def test_rules_are_deterministic_and_never_punitive() -> None:
    a = rules.individual_rules(alert()).candidates + rules.aggregate_rules(aggregate(
        inactive_without_recorded_departure=1, annualized_turnover=0.5, stability_score=10.0,
        month_end_active=[(1, 9), (2, 8), (3, 7), (4, 6)], active_alerts=9), LIMITS).candidates
    b = rules.individual_rules(alert()).candidates + rules.aggregate_rules(aggregate(
        inactive_without_recorded_departure=1, annualized_turnover=0.5, stability_score=10.0,
        month_end_active=[(1, 9), (2, 8), (3, 7), (4, 6)], active_alerts=9), LIMITS).candidates
    assert a == b
    for candidate in a:
        text = f"{candidate.title} {candidate.suggested_action} {candidate.explanation}".lower()
        assert not any(word in text for word in PUNITIVE), candidate.rule_id
    assert rules.RULE_VERSION == "rules-v1" and "Not a trained model" in rules.DISCLAIMER
