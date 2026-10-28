"""Shared search conventions: literal, case-insensitive keyword matching and organization-local date bounds.

All values are bound parameters; column names are never built from client input (sort fields are allow-lists in
each module). Keyword matching uses LIKE with "!" as the escape character because a backslash escape behaves
differently between MySQL and SQLite; %, _ and ! in a search term match literally.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, or_

LIKE_ESCAPE = "!"


def contains_pattern(term: str) -> str:
    """LIKE pattern matching `term` literally anywhere in the value."""
    escaped = (term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
               .replace("%", f"{LIKE_ESCAPE}%")
               .replace("_", f"{LIKE_ESCAPE}_"))
    return f"%{escaped}%"


def keyword_condition(term: str, *columns) -> ColumnElement[bool]:
    """Case-insensitive literal substring match against any of `columns`."""
    pattern = contains_pattern(term)
    return or_(*(column.ilike(pattern, escape=LIKE_ESCAPE) for column in columns))


def local_day_bounds(start: date | None, end: date | None, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """Inclusive organization-local dates as UTC-comparable [start, end) datetimes."""
    lower = datetime.combine(start, time.min, tzinfo=tz) if start else None
    upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz) if end else None
    return lower, upper
