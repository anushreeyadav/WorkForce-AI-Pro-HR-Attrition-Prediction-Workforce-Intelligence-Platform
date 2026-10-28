"""Pure attendance calculations. No database or HTTP access, so every rule is unit-testable.

Rules (also documented in README):
- Instants are UTC; wall-clock interpretation uses the organization time zone (ORG_TIMEZONE).
- Work date: the organization-local date of check-in. For an overnight shift, a check-in after
  midnight but before the shift's end time belongs to the previous day's shift.
- Late: check-in later than scheduled start + ATTENDANCE_LATE_GRACE_MINUTES (only when a shift is given).
- Work minutes: whole minutes between check-in and check-out, minus recorded break minutes.
- Overtime: work minutes above the shift's overtime threshold (or the configured default without a shift).
- Half day: at check-out, work minutes below 50% of the expected minutes (the shift's scheduled work
  minutes, or the default overtime threshold without a shift).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.enums import AttendanceStatus
from app.models.shift import Shift

MAX_SESSION = timedelta(hours=24)
HALF_DAY_RATIO = 0.5


@dataclass(frozen=True)
class AttendancePolicy:
    tz: ZoneInfo
    late_grace_minutes: int
    default_overtime_threshold_minutes: int


class AttendanceRuleError(ValueError):
    """A recorded interval or break that cannot be valid."""


def work_date_for(check_in: datetime, shift: Shift | None, tz: ZoneInfo) -> date:
    local = check_in.astimezone(tz)
    if shift is not None and shift.is_overnight and local.time() < shift.end_time:
        return local.date() - timedelta(days=1)
    return local.date()


def scheduled_start(work_date: date, shift: Shift, tz: ZoneInfo) -> datetime:
    return datetime.combine(work_date, shift.start_time, tzinfo=tz).astimezone(UTC)


def minutes_late(check_in: datetime, work_date: date, shift: Shift, tz: ZoneInfo) -> int:
    late = check_in - scheduled_start(work_date, shift, tz)
    return max(0, int(late.total_seconds() // 60))


def check_in_status(
    check_in: datetime, work_date: date, shift: Shift | None, policy: AttendancePolicy
) -> AttendanceStatus:
    if shift is not None and minutes_late(check_in, work_date, shift, policy.tz) > policy.late_grace_minutes:
        return AttendanceStatus.LATE
    return AttendanceStatus.PRESENT


def gross_minutes(check_in: datetime, check_out: datetime) -> int:
    if check_out <= check_in:
        raise AttendanceRuleError("Check-out must be after check-in")
    if check_out - check_in > MAX_SESSION:
        raise AttendanceRuleError("A single attendance session cannot exceed 24 hours")
    return int((check_out - check_in).total_seconds() // 60)


def work_minutes(check_in: datetime, check_out: datetime, break_minutes: int) -> int:
    gross = gross_minutes(check_in, check_out)
    if break_minutes < 0:
        raise AttendanceRuleError("Break minutes cannot be negative")
    if break_minutes > gross:
        raise AttendanceRuleError("Break minutes cannot exceed the time between check-in and check-out")
    return gross - break_minutes


def overtime_threshold(shift: Shift | None, policy: AttendancePolicy) -> int:
    return shift.overtime_threshold_minutes if shift else policy.default_overtime_threshold_minutes


def overtime_minutes(worked: int, shift: Shift | None, policy: AttendancePolicy) -> int:
    return max(0, worked - overtime_threshold(shift, policy))


def expected_minutes(shift: Shift | None, policy: AttendancePolicy) -> int:
    return shift.scheduled_work_minutes if shift else policy.default_overtime_threshold_minutes


def check_out_status(
    current: AttendanceStatus, worked: int, shift: Shift | None, policy: AttendancePolicy
) -> AttendanceStatus:
    if worked < expected_minutes(shift, policy) * HALF_DAY_RATIO:
        return AttendanceStatus.HALF_DAY
    return current


@dataclass(frozen=True)
class Computed:
    work_date: date
    status: AttendanceStatus
    work_minutes: int | None
    overtime_minutes: int | None


def compute(
    check_in: datetime,
    check_out: datetime | None,
    break_minutes: int,
    shift: Shift | None,
    policy: AttendancePolicy,
) -> Computed:
    """Derive every calculated field from the recorded facts."""
    work_date = work_date_for(check_in, shift, policy.tz)
    status = check_in_status(check_in, work_date, shift, policy)
    if check_out is None:
        return Computed(work_date, status, None, None)
    worked = work_minutes(check_in, check_out, break_minutes)
    return Computed(
        work_date=work_date,
        status=check_out_status(status, worked, shift, policy),
        work_minutes=worked,
        overtime_minutes=overtime_minutes(worked, shift, policy),
    )
