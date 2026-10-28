from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import AttendanceStatus

if TYPE_CHECKING:
    from app.models.employee import Employee
    from app.models.shift import Shift


class Attendance(TimestampMixin, Base):
    """One record per employee per work date.

    check_in/check_out are UTC instants. work_date is the organization-local date the shift belongs to
    (for overnight shifts, the date the shift started). work_minutes and overtime_minutes are computed
    by the server at check-out or correction time and are never accepted from clients.
    check_in is nullable only so a future absence-generation workflow can record ABSENT/ON_LEAVE days.
    """

    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "work_date", name="uq_attendance_records_employee_id_work_date"
        ),
        CheckConstraint(
            "check_out IS NULL OR (check_in IS NOT NULL AND check_out > check_in)",
            name="checkout_after_checkin",
        ),
        CheckConstraint("break_minutes >= 0", name="break_minutes_non_negative"),
        CheckConstraint("work_minutes IS NULL OR work_minutes >= 0", name="work_minutes_non_negative"),
        CheckConstraint(
            "overtime_minutes IS NULL OR overtime_minutes >= 0", name="overtime_minutes_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    shift_id: Mapped[int | None] = mapped_column(
        ForeignKey("shifts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    check_in: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    check_out: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    work_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="attendance_status"), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # One-directional on purpose: Employee and Shift carry no attendance attributes.
    employee: Mapped["Employee"] = relationship()
    shift: Mapped["Shift | None"] = relationship()

    @property
    def is_open(self) -> bool:
        return self.check_in is not None and self.check_out is None

    def __repr__(self) -> str:
        return f"<Attendance id={self.id} employee_id={self.employee_id} work_date={self.work_date}>"

# Activity-feed query indexes (see README, "Feed query indexes"): each serves a range/keyset filter on this
# timestamp with ORDER BY (timestamp, id) LIMIT n. InnoDB secondary indexes include the primary key.
Index("ix_attendance_records_created_at", Attendance.created_at)
