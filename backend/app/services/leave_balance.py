"""Leave policy resolution and balance bookkeeping.

Every balance change is one conditional UPDATE (check and change in a single statement), so two
concurrent transactions cannot both spend the same days: the loser's UPDATE matches no row and is
rejected. The no_overspend CHECK constraint on leave_balances is a second, database-level guard.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import LeaveBalance, LeavePolicy


class InsufficientBalanceError(Exception):
    def __init__(self, available: int) -> None:
        super().__init__(f"Insufficient leave balance: {available} day(s) available")
        self.available = available


class BalanceStateError(Exception):
    """The balance no longer matches the request (e.g. a concurrent change). Nothing was modified."""


def resolve_policy(db: Session, leave_type_id: int, department_id: int | None, on_date: date) -> LeavePolicy | None:
    """Active policy for the leave type effective on on_date; department-specific beats organization-wide."""
    department_match = LeavePolicy.department_id.is_(None)
    if department_id is not None:
        department_match = department_match | (LeavePolicy.department_id == department_id)
    return db.scalar(
        select(LeavePolicy)
        .where(
            LeavePolicy.leave_type_id == leave_type_id,
            LeavePolicy.is_active.is_(True),
            LeavePolicy.effective_from <= on_date,
            (LeavePolicy.effective_to.is_(None)) | (LeavePolicy.effective_to >= on_date),
            department_match,
        )
        # False sorts before True, so a department-specific policy (department_id IS NULL = False) wins.
        .order_by(LeavePolicy.department_id.is_(None), LeavePolicy.effective_from.desc(), LeavePolicy.id.desc())
        .limit(1)
    )


@dataclass(frozen=True)
class BalanceView:
    year: int
    allocated_days: int
    carried_forward_days: int
    used_days: int
    pending_days: int

    @property
    def available_days(self) -> int:
        return self.allocated_days + self.carried_forward_days - self.used_days - self.pending_days


def _get(db: Session, employee_id: int, leave_type_id: int, year: int) -> LeaveBalance | None:
    return db.scalar(
        select(LeaveBalance)
        .where(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.leave_type_id == leave_type_id,
            LeaveBalance.year == year,
        )
        .execution_options(populate_existing=True)
    )


def carried_forward(db: Session, employee_id: int, policy: LeavePolicy, year: int) -> int:
    """Unused days from the previous year, capped by the policy's carry_forward_days."""
    if policy.carry_forward_days == 0:
        return 0
    previous = _get(db, employee_id, policy.leave_type_id, year - 1)
    if previous is None:
        return 0
    return min(policy.carry_forward_days, max(0, previous.available_days))


def balance_view(db: Session, employee_id: int, policy: LeavePolicy | None, leave_type_id: int, year: int) -> BalanceView | None:
    """Read-only view: the stored balance if it exists, otherwise what would be allocated now."""
    existing = _get(db, employee_id, leave_type_id, year)
    if existing is not None:
        return BalanceView(year, existing.allocated_days, existing.carried_forward_days,
                           existing.used_days, existing.pending_days)
    if policy is None:
        return None
    return BalanceView(year, policy.annual_allowance_days, carried_forward(db, employee_id, policy, year), 0, 0)


def ensure_balance(db: Session, employee_id: int, policy: LeavePolicy, year: int) -> LeaveBalance:
    """Return the balance row, creating it from the policy on first use (race-safe via the unique key)."""
    existing = _get(db, employee_id, policy.leave_type_id, year)
    if existing is not None:
        return existing
    balance = LeaveBalance(
        employee_id=employee_id,
        leave_type_id=policy.leave_type_id,
        year=year,
        allocated_days=policy.annual_allowance_days,
        carried_forward_days=carried_forward(db, employee_id, policy, year),
        used_days=0,
        pending_days=0,
    )
    try:
        with db.begin_nested():
            db.add(balance)
    except IntegrityError:
        # Another transaction created it first; use theirs.
        existing = _get(db, employee_id, policy.leave_type_id, year)
        if existing is None:
            raise
        return existing
    return balance


def _apply(db: Session, balance_id: int, condition, values: dict) -> bool:
    result = db.execute(
        update(LeaveBalance)
        .where(LeaveBalance.id == balance_id, condition)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def reserve(db: Session, balance: LeaveBalance, days: int) -> None:
    """PENDING request created: pending += days, only if enough is available."""
    available = (
        LeaveBalance.allocated_days + LeaveBalance.carried_forward_days
        - LeaveBalance.used_days - LeaveBalance.pending_days
    )
    if not _apply(db, balance.id, available >= days, {"pending_days": LeaveBalance.pending_days + days}):
        current = _get(db, balance.employee_id, balance.leave_type_id, balance.year)
        raise InsufficientBalanceError(current.available_days if current else 0)


def consume(db: Session, balance: LeaveBalance, days: int) -> None:
    """Approval: move days from pending to used."""
    if not _apply(
        db, balance.id, LeaveBalance.pending_days >= days,
        {"pending_days": LeaveBalance.pending_days - days, "used_days": LeaveBalance.used_days + days},
    ):
        raise BalanceStateError("Pending balance does not cover this request")


def release_pending(db: Session, balance: LeaveBalance, days: int) -> None:
    """Rejection or cancellation of a PENDING request."""
    if not _apply(db, balance.id, LeaveBalance.pending_days >= days,
                  {"pending_days": LeaveBalance.pending_days - days}):
        raise BalanceStateError("Pending balance does not cover this request")


def restore_used(db: Session, balance: LeaveBalance, days: int) -> None:
    """Cancellation of an APPROVED request."""
    if not _apply(db, balance.id, LeaveBalance.used_days >= days, {"used_days": LeaveBalance.used_days - days}):
        raise BalanceStateError("Used balance does not cover this request")


def balance_for_request(db: Session, employee_id: int, leave_type_id: int, year: int) -> LeaveBalance:
    balance = _get(db, employee_id, leave_type_id, year)
    if balance is None:
        raise BalanceStateError("No balance exists for this request")
    return balance
