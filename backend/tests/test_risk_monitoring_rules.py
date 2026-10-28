"""Pure detection rules (no database). Scores are synthetic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import AlertPriority, RiskEventType
from app.services.risk_monitoring import (
    MonitoringConfig,
    detect,
    latest_and_previous,
    priority_for,
    raises_alert,
)

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
CONFIG = MonitoringConfig(alert_threshold=Decimal(70), change_points=Decimal(20), stale_after=timedelta(days=90),
                          medium=Decimal(40), high=Decimal(70))


def pred(pid: int, score: str, model: int = 1, days_ago: float = 1.0):
    return SimpleNamespace(id=pid, risk_score=Decimal(score), model_version_id=model,
                           predicted_at=NOW - timedelta(days=days_ago))


def kinds(events) -> list[RiskEventType]:
    return [e.event_type for e in events]


def test_latest_and_previous_ordering() -> None:
    older, newer = pred(5, "30", days_ago=3), pred(2, "80", days_ago=1)  # ids out of order
    latest, previous = latest_and_previous([newer, older])
    assert (latest.id, previous.id) == (2, 5)


def test_no_predictions_means_nothing_not_zero() -> None:
    assert latest_and_previous([]) == (None, None)


def test_different_model_versions_are_not_compared() -> None:
    latest, previous = latest_and_previous([pred(1, "20", model=1, days_ago=5), pred(2, "80", model=2)])
    assert latest.id == 2 and previous is None


def test_same_timestamp_duplicates_are_not_compared() -> None:
    a, b = pred(1, "20", days_ago=1), pred(2, "80", days_ago=1)
    latest, previous = latest_and_previous([a, b])
    assert latest.id == 2 and previous is None


@pytest.mark.parametrize(
    "previous, current, expected",
    [
        (None, "75", [RiskEventType.THRESHOLD_CROSSED]),              # baseline above threshold
        (None, "69.99", []),
        ("65", "71", [RiskEventType.THRESHOLD_CROSSED]),              # crossing, small change
        ("72", "75", []),                                             # already above: no new crossing
        ("40", "75", [RiskEventType.THRESHOLD_CROSSED, RiskEventType.SCORE_INCREASE]),
        ("20", "45", [RiskEventType.SCORE_INCREASE]),
        ("80", "55", [RiskEventType.SCORE_DECREASE]),
        ("50", "69", []),                                             # 19 points: below change threshold
        ("50", "30", [RiskEventType.SCORE_DECREASE]),                 # exactly 20 points
    ],
)
def test_detection(previous, current, expected) -> None:
    prev = pred(1, previous, days_ago=10) if previous is not None else None
    assert kinds(detect(pred(2, current), prev, NOW, CONFIG)) == expected


def test_stale_prediction() -> None:
    events = detect(pred(1, "30", days_ago=91), None, NOW, CONFIG)
    assert kinds(events) == [RiskEventType.STALE_PREDICTION]
    assert detect(pred(1, "30", days_ago=89), None, NOW, CONFIG) == []


def test_dedup_keys_are_stable_and_specific() -> None:
    events = detect(pred(9, "75"), pred(4, "40", days_ago=5), NOW, CONFIG)
    assert [e.dedup_key for e in events] == ["THRESHOLD_CROSSED:9", "SCORE_INCREASE:4:9"]
    assert [e.dedup_key for e in detect(pred(9, "75"), pred(4, "40", days_ago=5), NOW, CONFIG)] == [e.dedup_key for e in events]


@pytest.mark.parametrize("score, priority", [("39.99", AlertPriority.LOW), ("40", AlertPriority.MEDIUM),
                                              ("69.99", AlertPriority.MEDIUM), ("70", AlertPriority.HIGH)])
def test_priority_uses_score_only(score, priority) -> None:
    assert priority_for(Decimal(score), CONFIG) == priority


def test_which_events_raise_alerts() -> None:
    increase_low = detect(pred(2, "35"), pred(1, "10", days_ago=5), NOW, CONFIG)[0]
    increase_medium = detect(pred(2, "45"), pred(1, "20", days_ago=5), NOW, CONFIG)[0]
    decrease = detect(pred(2, "30"), pred(1, "60", days_ago=5), NOW, CONFIG)[0]
    stale = detect(pred(2, "30", days_ago=100), None, NOW, CONFIG)[0]
    crossing = detect(pred(2, "75"), None, NOW, CONFIG)[0]
    assert [raises_alert(e, CONFIG) for e in (increase_low, increase_medium, decrease, stale, crossing)] == [
        False, True, False, False, True]
