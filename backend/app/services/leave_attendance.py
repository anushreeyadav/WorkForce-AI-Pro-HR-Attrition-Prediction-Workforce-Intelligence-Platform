"""Read-only bridge between approved leave and attendance.

Nothing here creates, edits or deletes attendance. Reporting can call approved_leave_dates() to tell
"on approved leave" apart from "no attendance recorded", and conflicts_query() lists days where an
employee has attendance (a check-in) on a date covered by approved leave, for HR to review.
"""

from datetime import date

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session

from app.models import Attendance, LeaveRequest, LeaveRequestStatus
from app.services.leave_calendar import days_in_range


def approved_leave_dates(
    db: Session, employee_id: int, date_from: date, date_to: date
) -> dict[date, int]:
    """Calendar dates in [date_from, date_to] covered by APPROVED leave -> leave request id."""
    requests = db.scalars(
        select(LeaveRequest).where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == LeaveRequestStatus.APPROVED,
            LeaveRequest.start_date <= date_to,
            LeaveRequest.end_date >= date_from,
        )
    ).all()
    covered: dict[date, int] = {}
    for request in requests:
        for day in days_in_range(max(request.start_date, date_from), min(request.end_date, date_to)):
            covered[day] = request.id
    return covered


def conflicts_query() -> Select:
    """(Attendance, LeaveRequest) pairs where a check-in falls inside approved leave."""
    return (
        select(Attendance, LeaveRequest)
        .join(
            LeaveRequest,
            and_(
                LeaveRequest.employee_id == Attendance.employee_id,
                LeaveRequest.status == LeaveRequestStatus.APPROVED,
                Attendance.work_date >= LeaveRequest.start_date,
                Attendance.work_date <= LeaveRequest.end_date,
            ),
        )
        .where(Attendance.check_in.is_not(None))
    )


def conflict_dates(db: Session, request: LeaveRequest) -> list[date]:
    """Dates inside this request's range on which the employee already has a check-in."""
    return list(
        db.scalars(
            select(Attendance.work_date)
            .where(
                Attendance.employee_id == request.employee_id,
                Attendance.work_date >= request.start_date,
                Attendance.work_date <= request.end_date,
                Attendance.check_in.is_not(None),
            )
            .order_by(Attendance.work_date)
        ).all()
    )
