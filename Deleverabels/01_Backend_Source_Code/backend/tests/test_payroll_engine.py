"""Pure payroll arithmetic. Figures are illustrative test values, not real salaries or tax rules."""

from decimal import Decimal

import pytest

from app.models import CalculationMethod as M
from app.models import SalaryComponentKind as K
from app.services import payroll_engine as engine
from app.services.payroll_engine import ComponentInput, DayDeduction, PayrollCalculationError, calculate, money

D = Decimal


def comp(code, kind, method, value, order=100):
    return ComponentInput(code, code.title(), kind, method, D(value), order)


def test_basic_only() -> None:
    result = calculate(D("30000"), [], working_days=22)
    assert [(line.code, line.amount) for line in result.lines] == [("BASIC", D("30000.00"))]
    assert (result.gross_earnings, result.total_deductions, result.net_pay) == (D("30000.00"), D("0"), D("30000.00"))


def test_full_breakdown() -> None:
    result = calculate(
        D("40000.00"),
        [
            comp("HRA", K.EARNING, M.PERCENT_OF_BASE, "40", order=10),          # 16000.00
            comp("TRANSPORT", K.EARNING, M.FIXED, "1600", order=20),            # 1600.00
            comp("PF", K.DEDUCTION, M.PERCENT_OF_BASE, "12", order=10),         # 4800.00
            comp("WELFARE", K.DEDUCTION, M.FIXED, "200", order=20),             # 200.00
            comp("WITHHOLDING", K.TAX, M.PERCENT_OF_GROSS, "10"),               # 10% of 57600 = 5760.00
        ],
        working_days=22,
    )
    assert [line.code for line in result.lines] == ["BASIC", "HRA", "TRANSPORT", "PF", "WELFARE", "WITHHOLDING"]
    assert result.gross_earnings == D("57600.00")
    assert result.total_deductions == D("10760.00")
    assert result.net_pay == D("46840.00")
    hra = result.lines[1]
    assert (hra.rate, hra.basis, hra.amount) == (D("40"), D("40000.00"), D("16000.00"))
    # Totals are exact sums of the rounded lines.
    assert result.gross_earnings == sum(line.amount for line in result.earnings)
    assert result.total_deductions == sum(line.amount for line in result.deductions)


@pytest.mark.parametrize(
    "base, percent, expected",
    [
        ("10000.00", "33.33", "3333.00"),
        ("12345.67", "12.5", "1543.21"),   # 1543.20875 -> half-up
        ("100.01", "50", "50.01"),         # 50.005 -> half-up (banker's rounding would give 50.00)
        ("0.03", "50", "0.02"),            # 0.015 -> 0.02
    ],
)
def test_percentage_rounding_half_up(base, percent, expected) -> None:
    result = calculate(D(base), [comp("ALLOW", K.EARNING, M.PERCENT_OF_BASE, percent)], working_days=20)
    assert result.lines[1].amount == D(expected)


def test_no_float_drift() -> None:
    # 0.1 + 0.2 style errors cannot occur: all arithmetic is Decimal.
    result = calculate(D("0.10"), [comp("A", K.EARNING, M.FIXED, "0.20")], working_days=20)
    assert result.gross_earnings == D("0.30")
    assert isinstance(result.net_pay, Decimal)


def test_day_deductions() -> None:
    result = calculate(D("22000.00"), [], working_days=22,
                       day_deductions=[DayDeduction("UNPAID_LEAVE", "Unpaid leave", D(3)),
                                       DayDeduction("ABSENCE", "Recorded absence", D(0))])
    assert [line.code for line in result.deductions] == ["UNPAID_LEAVE"]  # zero-day items are omitted
    line = result.deductions[0]
    assert (line.amount, line.quantity, line.detail) == (D("3000.00"), D(3), {"working_days": 22})
    assert result.net_pay == D("19000.00")


def test_day_deduction_capped_at_working_days() -> None:
    result = calculate(D("20000"), [], working_days=20, day_deductions=[DayDeduction("ABSENCE", "Absence", D(25))])
    assert result.deductions[0].amount == D("20000.00")
    assert result.net_pay == D("0.00")


def test_percent_of_gross_uses_gross_before_deductions() -> None:
    result = calculate(D("10000"), [comp("BONUS", K.EARNING, M.FIXED, "2000"),
                                   comp("FUND", K.DEDUCTION, M.PERCENT_OF_GROSS, "10")], working_days=20)
    assert result.deductions[0].amount == D("1200.00")


@pytest.mark.parametrize(
    "base, components, message",
    [
        ("0", [], "Base salary must be greater than zero"),
        ("-5", [], "Base salary must be greater than zero"),
        ("1000", [comp("LOAN", K.DEDUCTION, M.FIXED, "1000.01")], "net pay would be negative"),
        ("1000", [comp("X", K.EARNING, M.PERCENT_OF_GROSS, "5")], "earnings cannot be a percentage of gross"),
        ("1000", [comp("X", K.EARNING, M.PERCENT_OF_BASE, "100.01")], "between 0 and 100"),
        ("1000", [comp("X", K.DEDUCTION, M.FIXED, "-1")], "cannot be negative"),
        ("1000", [comp("X", K.EARNING, M.FIXED, "1"), comp("X", K.DEDUCTION, M.FIXED, "1")], "unique"),
        ("1000", [comp("BASIC", K.EARNING, M.FIXED, "1")], "BASIC"),
    ],
)
def test_invalid_inputs_rejected(base, components, message) -> None:
    with pytest.raises(PayrollCalculationError, match=message):
        calculate(D(base), components, working_days=20)


def test_zero_working_days_rejected() -> None:
    with pytest.raises(PayrollCalculationError, match="no working days"):
        calculate(D("1000"), [], working_days=0)


def test_deterministic_regardless_of_input_order() -> None:
    components = [comp("B_ALLOW", K.EARNING, M.FIXED, "100", order=5), comp("A_ALLOW", K.EARNING, M.FIXED, "200", order=5),
                  comp("TAX", K.TAX, M.FIXED, "50")]
    first = calculate(D("1000"), components, working_days=20)
    second = calculate(D("1000"), list(reversed(components)), working_days=20)
    assert first == second
    assert [line.code for line in first.lines] == ["BASIC", "A_ALLOW", "B_ALLOW", "TAX"]


def test_tax_calculator_is_pluggable() -> None:
    class NoTax:
        def calculate(self, base, gross, tax_components):
            return []

    result = calculate(D("1000"), [comp("TAX", K.TAX, M.PERCENT_OF_GROSS, "20")], working_days=20, tax_calculator=NoTax())
    assert result.total_deductions == D("0")


def test_money_rounding_helper() -> None:
    assert money(D("2.675")) == D("2.68")
    assert money(D("-2.675")) == D("-2.68")
    assert engine.CENT == D("0.01")
