from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base, TimestampMixin
from app.models.enums import EmploymentStatus

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.user import User


class Employee(TimestampMixin, Base):
    """HR record for a person. Login credentials live on User; salary/payroll will get its own model."""

    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("employee_code"),
        UniqueConstraint("work_email"),
        # One login account maps to at most one employee record (multiple NULLs are allowed).
        UniqueConstraint("user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    work_email: Mapped[str] = mapped_column(String(255), nullable=False)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    designation: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_joining: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[EmploymentStatus] = mapped_column(
        Enum(EmploymentStatus, name="employment_status"),
        nullable=False,
        default=EmploymentStatus.ACTIVE,
        index=True,
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    user: Mapped["User | None"] = relationship(back_populates="employee")
    department: Mapped["Department"] = relationship(back_populates="employees")

    @validates("work_email")
    def _normalize_email(self, key: str, value: str) -> str:
        return value.strip().lower()

    def __repr__(self) -> str:
        return f"<Employee id={self.id} code={self.employee_code!r}>"

# Activity-feed query indexes (see README, "Feed query indexes"): each serves a range/keyset filter on this
# timestamp with ORDER BY (timestamp, id) LIMIT n. InnoDB secondary indexes include the primary key.
Index("ix_employees_created_at", Employee.created_at)
