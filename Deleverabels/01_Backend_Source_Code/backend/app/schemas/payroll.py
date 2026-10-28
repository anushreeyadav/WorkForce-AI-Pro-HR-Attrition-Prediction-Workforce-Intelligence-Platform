"""Payroll request/response schemas. Money is Decimal (serialized as a string, e.g. "45000.00")."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import CalculationMethod, PayFrequency, PayrollStatus, SalaryComponentKind
from app.schemas.attendance import AttendanceEmployeeRef
from app.schemas.common import PaginationParams, display_name, trimmed
from app.schemas.department import Description

PositiveId = Annotated[int, Field(gt=0)]
MoneyIn = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
PercentIn = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,29}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/\-]{0,99}$")


def _code(value: str) -> str:
    value = value.upper()
    if not _CODE.fullmatch(value):
        raise ValueError("Code must be 2-30 characters: a letter, then letters, digits or underscores")
    if value == "BASIC":
        raise ValueError("BASIC is reserved for the base salary line")
    return value


ComponentCode = Annotated[trimmed(2, 30), AfterValidator(_code)]


# --- Components --------------------------------------------------------------------------------


class SalaryComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ComponentCode
    name: display_name("Component name")
    description: Description = None
    kind: SalaryComponentKind
    calculation_method: CalculationMethod
    display_order: Annotated[int, Field(ge=0, le=1000)] = 100

    @model_validator(mode="after")
    def _check(self) -> "SalaryComponentCreate":
        if self.kind == SalaryComponentKind.EARNING and self.calculation_method == CalculationMethod.PERCENT_OF_GROSS:
            raise ValueError("Earnings cannot be a percentage of gross (gross depends on earnings)")
        return self


class SalaryComponentUpdate(BaseModel):
    """code, kind and calculation_method are fixed once created (records reference them)."""

    model_config = ConfigDict(extra="forbid")

    name: display_name("Component name") | None = None
    description: Description = None
    display_order: Annotated[int, Field(ge=0, le=1000)] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> "SalaryComponentUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for name in ("name", "display_order", "is_active"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class SalaryComponentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    kind: SalaryComponentKind
    calculation_method: CalculationMethod
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ComponentListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    kind: SalaryComponentKind | None = None
    is_active: bool | None = None


# --- Profiles ----------------------------------------------------------------------------------


class ProfileComponentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: PositiveId
    amount: MoneyIn | None = Field(default=None, description="For FIXED components")
    percentage: PercentIn | None = Field(default=None, description="For PERCENT_* components (0-100)")

    @model_validator(mode="after")
    def _one_value(self) -> "ProfileComponentIn":
        if (self.amount is None) == (self.percentage is None):
            raise ValueError("Provide exactly one of amount or percentage")
        return self


def _unique_components(items: list[ProfileComponentIn] | None) -> list[ProfileComponentIn] | None:
    if items is not None and len({i.component_id for i in items}) != len(items):
        raise ValueError("Each component may appear only once")
    return items


ComponentList = Annotated[list[ProfileComponentIn], Field(max_length=50), AfterValidator(_unique_components)]


class PayrollProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: PositiveId
    pay_frequency: PayFrequency = PayFrequency.MONTHLY
    base_salary: PositiveMoney = Field(description="Per pay period (monthly)")
    effective_from: date
    effective_to: date | None = None
    components: ComponentList = []

    @model_validator(mode="after")
    def _check(self) -> "PayrollProfileCreate":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        return self


class PayrollProfileUpdate(BaseModel):
    """employee_id is fixed. `components`, when sent, replaces the whole component list."""

    model_config = ConfigDict(extra="forbid")

    base_salary: PositiveMoney | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool | None = None
    components: ComponentList | None = None

    @model_validator(mode="after")
    def _check(self) -> "PayrollProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for name in ("base_salary", "effective_from", "is_active", "components"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ProfileComponentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    component_id: int
    code: str
    name: str
    kind: SalaryComponentKind
    calculation_method: CalculationMethod
    amount: Decimal | None
    percentage: Decimal | None


class PayrollProfileRead(BaseModel):
    id: int
    employee: AttendanceEmployeeRef
    pay_frequency: PayFrequency
    base_salary: Decimal
    effective_from: date
    effective_to: date | None
    is_active: bool
    components: list[ProfileComponentRead]
    created_at: datetime
    updated_at: datetime


class ProfileListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    employee_id: PositiveId | None = None
    is_active: bool | None = None


# --- Periods and records --------------------------------------------------------------------------


class PayrollPeriodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=2000, le=2100)]
    month: Annotated[int, Field(ge=1, le=12)]


class PayrollPeriodRead(BaseModel):
    id: int
    year: int
    month: int
    start_date: date
    end_date: date
    record_counts: dict[str, int] = Field(description="Number of payroll records per status")
    created_at: datetime


class PeriodListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=2000, le=2100)] | None = None


class CalculateRequest(BaseModel):
    """Omit employee_ids to calculate every employee with a salary profile in the period."""

    model_config = ConfigDict(extra="forbid")

    employee_ids: Annotated[list[PositiveId], Field(min_length=1, max_length=1000)] | None = None

    @field_validator("employee_ids")
    @classmethod
    def _unique(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("employee_ids must be unique")
        return value


class SkippedEmployee(BaseModel):
    employee_id: int
    reason: str


class CalculationResult(BaseModel):
    period_id: int
    created: int
    recalculated: int
    record_ids: list[int]
    skipped: list[SkippedEmployee]


class LineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    calculation_method: str
    rate: Decimal | None
    basis: Decimal | None
    quantity: Decimal | None
    amount: Decimal


class PeriodRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    year: int
    month: int
    start_date: date
    end_date: date


class PayrollRecordSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period: PeriodRef
    employee: AttendanceEmployeeRef
    status: PayrollStatus
    currency: str
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    has_warnings: bool
    calculated_at: datetime
    approved_at: datetime | None
    paid_at: datetime | None


class PayrollRecordRead(PayrollRecordSummary):
    profile_id: int
    base_salary: Decimal
    warnings: list[str]
    earnings: list[LineItemRead]
    deductions: list[LineItemRead]
    calculated_by_user_id: int | None
    approved_by_user_id: int | None
    paid_by_user_id: int | None
    payment_reference: str | None


class RecordListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: PayrollStatus | None = None
    department_id: PositiveId | None = None
    employee_id: PositiveId | None = None


class MyPayrollParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=2000, le=2100)] | None = None


class MarkPaidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_reference: str | None = Field(
        default=None, description="Reference to an external payment run (not a bank account number)"
    )

    @field_validator("payment_reference")
    @classmethod
    def _reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _REFERENCE.fullmatch(value):
            raise ValueError("payment_reference may contain letters, digits, spaces and . _ / - (max 100)")
        if re.fullmatch(r"[\d\s\-]{9,}", value):
            raise ValueError("payment_reference looks like an account number; use a payment-run reference")
        return value


# --- Payslip ----------------------------------------------------------------------------------


class PayslipEmployee(BaseModel):
    employee_code: str
    full_name: str
    department: str | None
    designation: str | None


class Payslip(BaseModel):
    record_id: int
    employee: PayslipEmployee
    period: PeriodRef
    currency: str
    earnings: list[LineItemRead]
    deductions: list[LineItemRead]
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    status: PayrollStatus
    generated_at: datetime
    note: str = "Computer-generated payslip. Tax lines reflect configured withholding, not a statutory tax computation."
