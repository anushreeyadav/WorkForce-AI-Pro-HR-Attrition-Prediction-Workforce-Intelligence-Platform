"""Working-day arithmetic. 2026-03-02 is a Monday; 2026-03-07/08 are Saturday/Sunday."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.leave_calendar import count_leave_days, days_in_range, is_leave_day, org_today

WEEKEND = [5, 6]


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (date(2026, 3, 2), date(2026, 3, 2), 1),    # single Monday
        (date(2026, 3, 2), date(2026, 3, 6), 5),    # Mon-Fri
        (date(2026, 3, 6), date(2026, 3, 9), 2),    # Fri-Mon spans a weekend
        (date(2026, 3, 7), date(2026, 3, 8), 0),    # weekend only
        (date(2026, 3, 2), date(2026, 3, 15), 10),  # two full weeks
        (date(2026, 2, 27), date(2026, 3, 2), 2),   # month boundary (Fri-Mon)
        (date(2028, 2, 28), date(2028, 3, 1), 3),   # leap day (Mon-Wed)
    ],
)
def test_working_days_exclude_weekends(start, end, expected) -> None:
    assert count_leave_days(start, end, WEEKEND, count_weekends=False) == expected


def test_policy_can_count_weekends() -> None:
    assert count_leave_days(date(2026, 3, 2), date(2026, 3, 8), WEEKEND, count_weekends=True) == 7
    assert count_leave_days(date(2026, 3, 7), date(2026, 3, 8), WEEKEND, count_weekends=True) == 2


def test_configurable_weekend() -> None:
    # Friday/Saturday weekend: Mon 2 - Sun 8 March has 5 working days (Mon-Thu + Sun).
    assert count_leave_days(date(2026, 3, 2), date(2026, 3, 8), [4, 5], count_weekends=False) == 5
    assert is_leave_day(date(2026, 3, 8), [4, 5], False)  # Sunday is a working day here
    assert not is_leave_day(date(2026, 3, 6), [4, 5], False)


def test_reversed_range_rejected() -> None:
    with pytest.raises(ValueError):
        count_leave_days(date(2026, 3, 3), date(2026, 3, 2), WEEKEND, False)


def test_days_in_range_inclusive() -> None:
    assert list(days_in_range(date(2026, 3, 1), date(2026, 3, 3))) == [
        date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3),
    ]


def test_org_today_uses_org_timezone() -> None:
    late_utc = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)  # 01:30 on 2 March in India
    assert org_today(late_utc, ZoneInfo("Asia/Kolkata")) == date(2026, 3, 2)
    assert org_today(late_utc, ZoneInfo("UTC")) == date(2026, 3, 1)
