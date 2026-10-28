"""Row-level visibility of employee records, enforced in queries rather than in the frontend."""

from dataclasses import dataclass

from sqlalchemy import Select, false, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import EMPLOYEE_READ_UNRESTRICTED
from app.models import Employee, EmploymentStatus, User, UserRole


@dataclass(frozen=True)
class EmployeeScope:
    unrestricted: bool = False
    department_id: int | None = None  # MANAGER: every employee in this department
    own_employee_id: int | None = None  # the caller's own employee record, if linked

    def allows(self, employee: Employee) -> bool:
        return (
            self.unrestricted
            or (self.department_id is not None and employee.department_id == self.department_id)
            or (self.own_employee_id is not None and employee.id == self.own_employee_id)
        )


def managed_department_id(employee: Employee | None) -> int | None:
    """The department a MANAGER may act on through their own employee record.

    Department-scoped access follows current employment: a manager whose own record is INACTIVE (has left) keeps
    self-service access to their own records but loses access to the department. Login deactivation remains the
    primary control; this covers departed managers whose login has not been deactivated yet.
    """
    if employee is None or employee.status == EmploymentStatus.INACTIVE:
        return None
    return employee.department_id


def employee_scope(db: Session, user: User) -> EmployeeScope:
    if user.role in EMPLOYEE_READ_UNRESTRICTED:
        return EmployeeScope(unrestricted=True)

    own = db.scalar(select(Employee).where(Employee.user_id == user.id))
    if own is None:
        # Not linked to an employee record: nothing is visible.
        return EmployeeScope()
    if user.role == UserRole.MANAGER:
        return EmployeeScope(department_id=managed_department_id(own), own_employee_id=own.id)
    return EmployeeScope(own_employee_id=own.id)


def apply_scope(stmt: Select, scope: EmployeeScope) -> Select:
    if scope.unrestricted:
        return stmt
    conditions = []
    if scope.department_id is not None:
        conditions.append(Employee.department_id == scope.department_id)
    if scope.own_employee_id is not None:
        conditions.append(Employee.id == scope.own_employee_id)
    return stmt.where(or_(*conditions)) if conditions else stmt.where(false())
