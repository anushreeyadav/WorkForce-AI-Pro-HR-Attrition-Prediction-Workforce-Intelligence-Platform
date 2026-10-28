"""Pure calculation rules. ORG timezone in tests is Asia/Kolkata (UTC+05:30)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import AttendanceStatus, Shift
from app.services import attendance_rules as rules
from tests.conftest import ORG_TZ, local

POLICY = rules.AttendancePolicy(tz=ORG_TZ, late_grace_minutes=15, default_overtime_threshold_minutes=480)
DAY = Shift(name="Day", start_time=time(9, 0), end_time=time(18, 0), break_minutes=60, overtime_threshold_minutes=480)
NIGHT = Shift(name="Night", start_time=time(22, 0), end_time=time(6, 0), break_minutes=30, overtime_threshold_minutes=450)


def test_shift_derived_properties() -> None:
    assert not DAY.is_overnight and DAY.duration_minutes == 540 and DAY.scheduled_work_minutes == 480
    assert NIGHT.is_overnight and NIGHT.duration_minutes == 480 and NIGHT.scheduled_work_minutes == 450


# --- Work date and timezone boundaries ---------------------------------------------------------------


def test_work_date_uses_org_timezone_not_utc() -> None:
    check_in = local(2026, 3, 11, 0, 30)  # 00:30 IST on the 11th == 19:00 UTC on the 10th
    assert check_in.date() == date(2026, 3, 10)
    assert rules.work_date_for(check_in, None, ORG_TZ) == date(2026, 3, 11)


@pytest.mark.parametrize(
    "check_in, expected",
    [
        (local(2026, 3, 10, 21, 50), date(2026, 3, 10)),  # early arrival before midnight
        (local(2026, 3, 10, 23, 59), date(2026, 3, 10)),
        (local(2026, 3, 11, 0, 30), date(2026, 3, 10)),   # after midnight, still the 10th's shift
        (local(2026, 3, 11, 5, 59), date(2026, 3, 10)),
        (local(2026, 3, 11, 6, 0), date(2026, 3, 11)),    # at/after end time: a new work date
        (local(2026, 3, 11, 12, 0), date(2026, 3, 11)),
    ],
)
def test_overnight_work_date(check_in: datetime, expected: date) -> None:
    assert rules.work_date_for(check_in, NIGHT, ORG_TZ) == expected


def test_day_shift_after_midnight_is_same_local_date() -> None:
    assert rules.work_date_for(local(2026, 3, 11, 0, 30), DAY, ORG_TZ) == date(2026, 3, 11)


def test_scheduled_start_respects_dst() -> None:
    new_york = ZoneInfo("America/New_York")  # DST starts 2026-03-08
    before = rules.scheduled_start(date(2026, 3, 7), DAY, new_york)
    after = rules.scheduled_start(date(2026, 3, 9), DAY, new_york)
    assert before == datetime(2026, 3, 7, 14, 0, tzinfo=UTC)
    assert after == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)


# --- Lateness -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "check_in, expected",
    [
        (local(2026, 3, 10, 8, 30), AttendanceStatus.PRESENT),
        (local(2026, 3, 10, 9, 15), AttendanceStatus.PRESENT),      # exactly at grace limit
        (local(2026, 3, 10, 9, 15, 59), AttendanceStatus.PRESENT),  # 15 whole minutes
        (local(2026, 3, 10, 9, 16), AttendanceStatus.LATE),
        (local(2026, 3, 10, 13, 0), AttendanceStatus.LATE),
    ],
)
def test_late_arrival_day_shift(check_in: datetime, expected: AttendanceStatus) -> None:
    work_date = rules.work_date_for(check_in, DAY, ORG_TZ)
    assert rules.check_in_status(check_in, work_date, DAY, POLICY) == expected


def test_late_arrival_overnight_after_midnight() -> None:
    check_in = local(2026, 3, 11, 0, 30)
    work_date = rules.work_date_for(check_in, NIGHT, ORG_TZ)
    assert rules.minutes_late(check_in, work_date, NIGHT, ORG_TZ) == 150
    assert rules.check_in_status(check_in, work_date, NIGHT, POLICY) == AttendanceStatus.LATE


def test_no_shift_is_never_late() -> None:
    check_in = local(2026, 3, 10, 23, 0)
    assert rules.check_in_status(check_in, date(2026, 3, 10), None, POLICY) == AttendanceStatus.PRESENT


def test_zero_grace() -> None:
    strict = rules.AttendancePolicy(tz=ORG_TZ, late_grace_minutes=0, default_overtime_threshold_minutes=480)
    assert rules.check_in_status(local(2026, 3, 10, 9, 1), date(2026, 3, 10), DAY, strict) == AttendanceStatus.LATE


# --- Durations, breaks, overtime ----------------------------------------------------------------------


def test_work_minutes_subtracts_break() -> None:
    assert rules.work_minutes(local(2026, 3, 10, 9, 0), local(2026, 3, 10, 18, 0), 60) == 480


def test_partial_minutes_are_truncated() -> None:
    assert rules.gross_minutes(local(2026, 3, 10, 9, 0), local(2026, 3, 10, 9, 0, 59)) == 0
    assert rules.gross_minutes(local(2026, 3, 10, 9, 0), local(2026, 3, 10, 9, 1, 59)) == 1


@pytest.mark.parametrize(
    "check_in, check_out, break_minutes, message",
    [
        (local(2026, 3, 10, 9), local(2026, 3, 10, 9), 0, "after check-in"),
        (local(2026, 3, 10, 9), local(2026, 3, 10, 8), 0, "after check-in"),
        (local(2026, 3, 10, 9), local(2026, 3, 11, 9, 1), 0, "24 hours"),
        (local(2026, 3, 10, 9), local(2026, 3, 10, 10), 61, "Break minutes cannot exceed"),
        (local(2026, 3, 10, 9), local(2026, 3, 10, 10), -1, "negative"),
    ],
)
def test_invalid_intervals(check_in, check_out, break_minutes, message) -> None:
    with pytest.raises(rules.AttendanceRuleError, match=message):
        rules.work_minutes(check_in, check_out, break_minutes)


def test_overtime_uses_shift_threshold_or_default() -> None:
    assert rules.overtime_minutes(510, DAY, POLICY) == 30
    assert rules.overtime_minutes(480, DAY, POLICY) == 0
    assert rules.overtime_minutes(500, NIGHT, POLICY) == 50  # night threshold 450
    assert rules.overtime_minutes(500, None, POLICY) == 20   # default 480


@pytest.mark.parametrize(
    "worked, shift, expected",
    [
        (239, DAY, AttendanceStatus.HALF_DAY),  # < 50% of 480
        (240, DAY, AttendanceStatus.PRESENT),
        (224, NIGHT, AttendanceStatus.HALF_DAY),  # < 50% of 450
        (225, NIGHT, AttendanceStatus.PRESENT),
        (239, None, AttendanceStatus.HALF_DAY),  # default expectation 480
    ],
)
def test_half_day(worked: int, shift, expected) -> None:
    assert rules.check_out_status(AttendanceStatus.PRESENT, worked, shift, POLICY) == expected


def test_half_day_overrides_late_but_full_day_keeps_late() -> None:
    assert rules.check_out_status(AttendanceStatus.LATE, 100, DAY, POLICY) == AttendanceStatus.HALF_DAY
    assert rules.check_out_status(AttendanceStatus.LATE, 400, DAY, POLICY) == AttendanceStatus.LATE


def test_compute_overnight_shift_end_to_end() -> None:
    result = rules.compute(local(2026, 3, 10, 21, 55), local(2026, 3, 11, 6, 30), 30, NIGHT, POLICY)
    assert result.work_date == date(2026, 3, 10)
    assert result.status == AttendanceStatus.PRESENT
    assert result.work_minutes == 8 * 60 + 35 - 30  # 485
    assert result.overtime_minutes == 35


def test_compute_open_record_has_no_durations() -> None:
    result = rules.compute(local(2026, 3, 10, 9, 30), None, 0, DAY, POLICY)
    assert (result.status, result.work_minutes, result.overtime_minutes) == (AttendanceStatus.LATE, None, None)


def test_max_session_constant() -> None:
    assert rules.MAX_SESSION == timedelta(hours=24)
