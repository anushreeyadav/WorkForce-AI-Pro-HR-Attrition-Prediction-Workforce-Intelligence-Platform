"""Pure, deterministic payroll arithmetic. No database, no clock, no floats.

Rules (also documented in README):
- All money is Decimal. Every line is rounded half-up to 0.01 on its own; gross earnings, total
  deductions and net pay are exact sums/differences of the rounded lines, so a payslip always adds up.
- Order: BASIC, then earnings (FIXED or PERCENT_OF_BASE), then leave/absence adjustments (only when
  enabled), then deductions (FIXED, PERCENT_OF_BASE, PERCENT_OF_GROSS), then tax via a TaxCalculator.
- PERCENT_OF_GROSS uses gross earnings (before any deductions).
- Leave/absence deduction = base salary x days / working days in the period, rounded once.
- Net pay may not be negative; such a calculation is rejected, not clamped.
- Lines are ordered by (display_order, code) so identical inputs always give identical output.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from app.models.enums import CalculationMethod, SalaryComponentKind

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
BASIC_CODE = "BASIC"


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class PayrollCalculationError(ValueError):
    """The inputs cannot produce a valid payroll (e.g. net pay would be negative)."""


@dataclass(frozen=True)
class ComponentInput:
    code: str
    name: str
    kind: SalaryComponentKind
    method: CalculationMethod
    value: Decimal  # an amount for FIXED, a percentage (0-100) otherwise
    display_order: int = 100


@dataclass(frozen=True)
class DayDeduction:
    """A per-day salary deduction (unpaid leave or recorded absence)."""

    code: str
    name: str
    days: Decimal
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Line:
    kind: SalaryComponentKind
    code: str
    name: str
    method: str
    amount: Decimal
    rate: Decimal | None = None
    basis: Decimal | None = None
    quantity: Decimal | None = None
    detail: dict | None = None


@dataclass(frozen=True)
class PayrollResult:
    lines: list[Line]
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal

    @property
    def earnings(self) -> list[Line]:
        return [line for line in self.lines if line.kind == SalaryComponentKind.EARNING]

    @property
    def deductions(self) -> list[Line]:
        return [line for line in self.lines if line.kind != SalaryComponentKind.EARNING]


def _component_line(component: ComponentInput, base: Decimal, gross: Decimal) -> Line:
    if component.method == CalculationMethod.FIXED:
        if component.value < 0:
            raise PayrollCalculationError(f"{component.code}: amount cannot be negative")
        return Line(component.kind, component.code, component.name, component.method.value, money(component.value))

    if not (Decimal(0) <= component.value <= HUNDRED):
        raise PayrollCalculationError(f"{component.code}: percentage must be between 0 and 100")
    if component.method == CalculationMethod.PERCENT_OF_BASE:
        basis = base
    elif component.method == CalculationMethod.PERCENT_OF_GROSS:
        if component.kind == SalaryComponentKind.EARNING:
            raise PayrollCalculationError(f"{component.code}: earnings cannot be a percentage of gross")
        basis = gross
    else:  # pragma: no cover - enum is exhaustive
        raise PayrollCalculationError(f"{component.code}: unsupported calculation method")
    return Line(component.kind, component.code, component.name, component.method.value,
                money(basis * component.value / HUNDRED), rate=component.value, basis=basis)


class TaxCalculator(Protocol):
    """Separate interface so a jurisdiction-specific implementation can replace the default later."""

    def calculate(self, base: Decimal, gross: Decimal, tax_components: list[ComponentInput]) -> list[Line]: ...


class ConfiguredComponentTax:
    """Default: applies the TAX components configured on the profile (fixed or percentage).

    This is NOT a statutory tax computation (no slabs, exemptions, regimes or annualisation). It only
    deducts what HR has configured. Treat results as configured withholding, not tax advice.
    """

    def calculate(self, base: Decimal, gross: Decimal, tax_components: list[ComponentInput]) -> list[Line]:
        return [_component_line(component, base, gross) for component in tax_components]


def calculate(
    base_salary: Decimal,
    components: list[ComponentInput],
    *,
    working_days: int,
    day_deductions: list[DayDeduction] = (),  # type: ignore[assignment]
    tax_calculator: TaxCalculator | None = None,
) -> PayrollResult:
    if base_salary <= 0:
        raise PayrollCalculationError("Base salary must be greater than zero")
    if working_days <= 0:
        raise PayrollCalculationError("The period has no working days")
    if len({c.code for c in components}) != len(components) or any(c.code == BASIC_CODE for c in components):
        raise PayrollCalculationError("Component codes must be unique and must not reuse BASIC")

    base = money(base_salary)
    ordered = sorted(components, key=lambda c: (c.display_order, c.code))
    by_kind = {kind: [c for c in ordered if c.kind == kind] for kind in SalaryComponentKind}

    lines = [Line(SalaryComponentKind.EARNING, BASIC_CODE, "Basic salary", CalculationMethod.FIXED.value, base)]
    for component in by_kind[SalaryComponentKind.EARNING]:
        lines.append(_component_line(component, base, Decimal(0)))
    gross = sum((line.amount for line in lines), Decimal(0))

    for item in day_deductions:
        if item.days < 0:
            raise PayrollCalculationError(f"{item.code}: days cannot be negative")
        if item.days == 0:
            continue
        days = min(item.days, Decimal(working_days))
        lines.append(Line(
            SalaryComponentKind.DEDUCTION, item.code, item.name, "PER_WORKING_DAY",
            money(base * days / Decimal(working_days)), basis=base, quantity=days,
            detail={"working_days": working_days, **item.detail},
        ))
    for component in by_kind[SalaryComponentKind.DEDUCTION]:
        lines.append(_component_line(component, base, gross))
    lines.extend((tax_calculator or ConfiguredComponentTax()).calculate(base, gross, by_kind[SalaryComponentKind.TAX]))

    deductions = sum((line.amount for line in lines if line.kind != SalaryComponentKind.EARNING), Decimal(0))
    net = gross - deductions
    if net < 0:
        raise PayrollCalculationError("Deductions exceed gross earnings; net pay would be negative")
    return PayrollResult(lines=lines, gross_earnings=gross, total_deductions=deductions, net_pay=net)
