"""Pure leave-day arithmetic. Dates are organization-local calendar dates (ORG_TIMEZONE).

Working-day calendar: every day except LEAVE_WEEKEND_DAYS (default Saturday and Sunday) counts, unless
the policy sets count_weekends, in which case every calendar day counts. There is no public-holiday
calendar yet, so holidays inside a request are counted as working days.
"""

from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def org_today(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date()


def days_in_range(start: date, end: date) -> Iterator[date]:
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def is_leave_day(day: date, weekend_days: Iterable[int], count_weekends: bool) -> bool:
    return count_weekends or day.weekday() not in set(weekend_days)


def count_leave_days(start: date, end: date, weekend_days: Iterable[int], count_weekends: bool) -> int:
    if end < start:
        raise ValueError("end date is before start date")
    weekend = frozenset(weekend_days)
    return sum(1 for day in days_in_range(start, end) if is_leave_day(day, weekend, count_weekends))
