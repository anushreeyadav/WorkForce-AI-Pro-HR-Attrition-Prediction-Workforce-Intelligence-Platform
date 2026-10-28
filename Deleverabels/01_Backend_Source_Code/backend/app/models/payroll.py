"""Payroll models. Money is NUMERIC(12,2) (Decimal in Python); percentages are NUMERIC(5,2).

Nothing here is reachable from Employee or the employee endpoints: relationships point from payroll
tables to employees, never the other way round.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import CalculationMethod, PayFrequency, PayrollStatus, SalaryComponentKind

if TYPE_CHECKING:
    from app.models.employee import Employee

Money = Numeric(12, 2)
Percent = Numeric(5, 2)


class SalaryComponent(TimestampMixin, Base):
    """Catalog entry for an earning, deduction or tax line. Values are set per payroll profile."""

    __tablename__ = "salary_components"
    __table_args__ = (
        UniqueConstraint("code"),
        UniqueConstraint("name"),
        CheckConstraint(
            "NOT (kind = 'EARNING' AND calculation_method = 'PERCENT_OF_GROSS')",
            name="earning_not_percent_of_gross",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[SalaryComponentKind] = mapped_column(
        Enum(SalaryComponentKind, name="salary_component_kind"), nullable=False
    )
    calculation_method: Mapped[CalculationMethod] = mapped_column(
        Enum(CalculationMethod, name="salary_calculation_method"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())


class PayrollProfile(TimestampMixin, Base):
    """An employee's salary structure for an effective date range (base_salary is per pay period)."""

    __tablename__ = "payroll_profiles"
    __table_args__ = (
        CheckConstraint("base_salary > 0", name="base_salary_positive"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="effective_range"),
        Index("ix_payroll_profiles_employee_effective", "employee_id", "effective_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    pay_frequency: Mapped[PayFrequency] = mapped_column(
        Enum(PayFrequency, name="pay_frequency"), nullable=False, default=PayFrequency.MONTHLY
    )
    base_salary: Mapped[Decimal] = mapped_column(Money, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    employee: Mapped["Employee"] = relationship()
    components: Mapped[list["PayrollProfileComponent"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", order_by="PayrollProfileComponent.id"
    )


class PayrollProfileComponent(Base):
    """A component's value on one profile: an amount (FIXED) or a percentage (PERCENT_*), never both."""

    __tablename__ = "payroll_profile_components"
    __table_args__ = (
        UniqueConstraint("profile_id", "component_id", name="uq_payroll_profile_components_profile_id_component_id"),
        CheckConstraint("(amount IS NULL) <> (percentage IS NULL)", name="amount_xor_percentage"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="amount_non_negative"),
        CheckConstraint("percentage IS NULL OR (percentage >= 0 AND percentage <= 100)", name="percentage_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("payroll_profiles.id", ondelete="CASCADE"), nullable=False)
    component_id: Mapped[int] = mapped_column(
        ForeignKey("salary_components.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    percentage: Mapped[Decimal | None] = mapped_column(Percent, nullable=True)

    profile: Mapped["PayrollProfile"] = relationship(back_populates="components")
    component: Mapped["SalaryComponent"] = relationship()


class PayrollPeriod(TimestampMixin, Base):
    """A calendar month. Dates are organization-local (ORG_TIMEZONE)."""

    __tablename__ = "payroll_periods"
    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_payroll_periods_year_month"),
        CheckConstraint("month >= 1 AND month <= 12", name="month_range"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="year_range"),
        CheckConstraint("end_date >= start_date", name="date_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PayrollRecord(TimestampMixin, Base):
    """One employee's payroll for one period. Amounts are snapshots; later profile edits never change them.

    Status flow: CALCULATED -> APPROVED -> PAID. Only CALCULATED records may be recalculated.
    """

    __tablename__ = "payroll_records"
    __table_args__ = (
        UniqueConstraint("period_id", "employee_id", name="uq_payroll_records_period_id_employee_id"),
        CheckConstraint("base_salary > 0", name="base_salary_positive"),
        CheckConstraint("gross_earnings >= 0", name="gross_non_negative"),
        CheckConstraint("total_deductions >= 0", name="deductions_non_negative"),
        CheckConstraint("net_pay >= 0", name="net_non_negative"),
        # Within half a cent: exact on MySQL DECIMAL(12,2); tolerant of SQLite, which stores NUMERIC as REAL.
        CheckConstraint(
            "ABS(net_pay - (gross_earnings - total_deductions)) < 0.005", name="net_is_gross_minus_deductions"
        ),
        Index("ix_payroll_records_employee_period", "employee_id", "period_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("payroll_periods.id", ondelete="RESTRICT"), nullable=False)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("payroll_profiles.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[PayrollStatus] = mapped_column(
        Enum(PayrollStatus, name="payroll_status"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    base_salary: Mapped[Decimal] = mapped_column(Money, nullable=False)
    gross_earnings: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total_deductions: Mapped[Decimal] = mapped_column(Money, nullable=False)
    net_pay: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # Data-quality notes from calculation (e.g. open attendance, mid-period joining); never silently assumed away.
    warnings: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    calculated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    paid_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # A reference to an external payment run. Never a bank account number.
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    period: Mapped["PayrollPeriod"] = relationship()
    employee: Mapped["Employee"] = relationship()
    line_items: Mapped[list["PayrollLineItem"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="PayrollLineItem.sequence"
    )


class PayrollLineItem(Base):
    """One itemized earning, deduction or tax line, with the inputs needed to re-verify it."""

    __tablename__ = "payroll_line_items"
    __table_args__ = (
        UniqueConstraint("record_id", "sequence", name="uq_payroll_line_items_record_id_sequence"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint("rate IS NULL OR (rate >= 0 AND rate <= 100)", name="rate_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("payroll_records.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[SalaryComponentKind] = mapped_column(
        Enum(SalaryComponentKind, name="salary_component_kind"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    calculation_method: Mapped[str] = mapped_column(String(30), nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(Percent, nullable=True)
    basis: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    record: Mapped["PayrollRecord"] = relationship(back_populates="line_items")
