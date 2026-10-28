from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import LeaveRequestStatus

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.employee import Employee

MAX_LEAVE_DAYS = 366


class LeaveType(TimestampMixin, Base):
    __tablename__ = "leave_types"
    __table_args__ = (UniqueConstraint("name"), UniqueConstraint("code"))

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Stable identifier for integrations and reports; never changed after creation.
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    def __repr__(self) -> str:
        return f"<LeaveType id={self.id} code={self.code!r}>"


class LeavePolicy(TimestampMixin, Base):
    """Allowance rules for a leave type. department_id NULL = organization-wide.

    Resolution for an employee and date: an active policy for the employee's department whose effective
    range covers the date wins over an active organization-wide one.
    """

    __tablename__ = "leave_policies"
    __table_args__ = (
        CheckConstraint(
            f"annual_allowance_days >= 0 AND annual_allowance_days <= {MAX_LEAVE_DAYS}",
            name="allowance_range",
        ),
        CheckConstraint(
            f"max_consecutive_days IS NULL OR (max_consecutive_days > 0 AND max_consecutive_days <= {MAX_LEAVE_DAYS})",
            name="max_consecutive_range",
        ),
        CheckConstraint(
            f"carry_forward_days >= 0 AND carry_forward_days <= {MAX_LEAVE_DAYS}", name="carry_forward_range"
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="effective_range"
        ),
        Index("ix_leave_policies_type_department", "leave_type_id", "department_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    leave_type_id: Mapped[int] = mapped_column(
        ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    annual_allowance_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_consecutive_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    carry_forward_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # False: only working days (per LEAVE_WEEKEND_DAYS) count. True: every calendar day counts.
    count_weekends: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    leave_type: Mapped["LeaveType"] = relationship()
    department: Mapped["Department | None"] = relationship()

    def __repr__(self) -> str:
        return f"<LeavePolicy id={self.id} leave_type_id={self.leave_type_id} department_id={self.department_id}>"


class LeaveRequest(TimestampMixin, Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="date_range"),
        CheckConstraint(f"working_days > 0 AND working_days <= {MAX_LEAVE_DAYS}", name="working_days_range"),
        Index("ix_leave_requests_employee_dates", "employee_id", "start_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    leave_type_id: Mapped[int] = mapped_column(
        ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # The policy that was applied when the request was made.
    policy_id: Mapped[int] = mapped_column(ForeignKey("leave_policies.id", ondelete="RESTRICT"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    working_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[LeaveRequestStatus] = mapped_column(
        Enum(LeaveRequestStatus, name="leave_request_status"),
        nullable=False,
        default=LeaveRequestStatus.PENDING,
        index=True,
    )
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancelled_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancellation_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)

    employee: Mapped["Employee"] = relationship()
    leave_type: Mapped["LeaveType"] = relationship()
    policy: Mapped["LeavePolicy"] = relationship()

    @property
    def leave_year(self) -> int:
        return self.start_date.year

    def __repr__(self) -> str:
        return f"<LeaveRequest id={self.id} employee_id={self.employee_id} status={self.status}>"


class LeaveBalance(TimestampMixin, Base):
    """Per employee, leave type and calendar year. Created on first use from the applicable policy.

    Changes are made only through conditional UPDATE statements (see app.services.leave_balance), and
    the CHECK constraints make overspending or negative values impossible at the database level.
    """

    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "leave_type_id", "year", name="uq_leave_balances_employee_id_leave_type_id_year"
        ),
        CheckConstraint("year >= 2000 AND year <= 2100", name="year_range"),
        CheckConstraint("allocated_days >= 0", name="allocated_non_negative"),
        CheckConstraint("carried_forward_days >= 0", name="carried_non_negative"),
        CheckConstraint("used_days >= 0", name="used_non_negative"),
        CheckConstraint("pending_days >= 0", name="pending_non_negative"),
        CheckConstraint(
            "used_days + pending_days <= allocated_days + carried_forward_days", name="no_overspend"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    leave_type_id: Mapped[int] = mapped_column(
        ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_days: Mapped[int] = mapped_column(Integer, nullable=False)
    carried_forward_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    leave_type: Mapped["LeaveType"] = relationship()

    @property
    def available_days(self) -> int:
        return self.allocated_days + self.carried_forward_days - self.used_days - self.pending_days

# Activity-feed query indexes (see README, "Feed query indexes"): each serves a range/keyset filter on this
# timestamp with ORDER BY (timestamp, id) LIMIT n. InnoDB secondary indexes include the primary key.
Index("ix_leave_requests_created_at", LeaveRequest.created_at)
Index("ix_leave_requests_reviewed_at", LeaveRequest.reviewed_at)
Index("ix_leave_requests_cancelled_at", LeaveRequest.cancelled_at)
