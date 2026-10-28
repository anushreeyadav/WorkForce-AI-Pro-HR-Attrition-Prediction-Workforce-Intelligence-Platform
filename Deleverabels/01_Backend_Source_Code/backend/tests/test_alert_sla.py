"""Pure review-deadline (SLA) calculations. No database."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.models import AlertPriority, AlertStatus
from app.services import alert_sla as sla

POLICY = sla.SlaPolicy(hours={AlertPriority.HIGH: 24, AlertPriority.MEDIUM: 72, AlertPriority.LOW: 168},
                       approaching_percent=75)
START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def state(priority=AlertPriority.HIGH, status=AlertStatus.OPEN, after=timedelta(0), acknowledged=None, resolved=None):
    return sla.sla_status(priority, status, START, acknowledged, resolved, START + after, POLICY)


@pytest.mark.parametrize("priority, hours", [(AlertPriority.HIGH, 24), (AlertPriority.MEDIUM, 72), (AlertPriority.LOW, 168)])
def test_deadline_for_every_priority(priority, hours) -> None:
    result = state(priority)
    assert result.review_window_hours == hours
    assert result.review_due_at == START + timedelta(hours=hours)
    assert result.approaching_at == START + timedelta(hours=hours * 0.75)
    assert set(POLICY.hours) == set(AlertPriority)  # every existing priority has a deadline


@pytest.mark.parametrize("after, expected", [
    (timedelta(0), "ON_TRACK"),
    (timedelta(hours=18) - timedelta(microseconds=1), "ON_TRACK"),
    (timedelta(hours=18), "APPROACHING"),  # 75% of 24h, inclusive
    (timedelta(hours=24) - timedelta(microseconds=1), "APPROACHING"),
    (timedelta(hours=24), "OVERDUE"),  # the deadline instant itself is overdue
    (timedelta(days=30), "OVERDUE"),
])
def test_open_alert_boundaries(after, expected) -> None:
    assert state(after=after).state == expected


def test_acknowledged_and_closed_states() -> None:
    on_time = state(status=AlertStatus.ACKNOWLEDGED, after=timedelta(days=5), acknowledged=START + timedelta(hours=24))
    assert (on_time.state, on_time.met_deadline) == ("ACKNOWLEDGED", True)
    late = state(status=AlertStatus.ACKNOWLEDGED, after=timedelta(days=5), acknowledged=START + timedelta(hours=25))
    assert (late.state, late.met_deadline) == ("ACKNOWLEDGED", False)
    for status in (AlertStatus.RESOLVED, AlertStatus.DISMISSED):
        closed = state(status=status, after=timedelta(days=9), resolved=START + timedelta(hours=2))
        assert (closed.state, closed.met_deadline) == ("CLOSED", True)
        # Acknowledged late, then resolved: the first review action decides.
        closed = state(status=status, after=timedelta(days=9), acknowledged=START + timedelta(days=2),
                       resolved=START + timedelta(days=3))
        assert (closed.state, closed.met_deadline) == ("CLOSED", False)
    assert state(status=AlertStatus.OPEN).met_deadline is None


def test_timezone_aware_inputs_required_and_offsets_respected() -> None:
    with pytest.raises(ValueError):
        sla.sla_status(AlertPriority.HIGH, AlertStatus.OPEN, START.replace(tzinfo=None), None, None, START, POLICY)
    with pytest.raises(ValueError):
        sla.sla_status(AlertPriority.HIGH, AlertStatus.OPEN, START, None, None, datetime(2026, 10, 2, 9), POLICY)
    ist = timezone(timedelta(hours=5, minutes=30))
    same_instant = (START + timedelta(hours=24)).astimezone(ist)  # 14:30 local on the next day
    assert sla.sla_status(AlertPriority.HIGH, AlertStatus.OPEN, START, None, None, same_instant, POLICY).state == "OVERDUE"
    assert sla.sla_status(AlertPriority.HIGH, AlertStatus.OPEN, START, None, None,
                          same_instant - timedelta(seconds=1), POLICY).state == "APPROACHING"


def test_higher_priority_shortens_deadline_from_the_same_start() -> None:
    after = timedelta(hours=30)
    assert state(AlertPriority.MEDIUM, after=after).state == "ON_TRACK"
    assert state(AlertPriority.HIGH, after=after).state == "OVERDUE"


def test_policy_from_settings() -> None:
    from tests.test_config import make_settings

    settings = make_settings(alert_review_sla_hours_high=4, alert_review_sla_hours_medium=8,
                             alert_review_sla_hours_low=8, alert_review_approaching_percent=50)
    policy = sla.SlaPolicy.from_settings(settings)
    assert policy.hours == {AlertPriority.HIGH: 4, AlertPriority.MEDIUM: 8, AlertPriority.LOW: 8}
    assert policy.approaching_percent == 50
