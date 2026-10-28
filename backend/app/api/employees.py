"""Employee records. There is deliberately no DELETE: status changes preserve HR history."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.models import Department, Employee, User
from app.schemas.common import Page
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeListParams,
    EmployeeRead,
    EmployeeSearchParams,
    EmployeeStatusUpdate,
    EmployeeUpdate,
)
from app.services.audit import apply_changes, record_audit
from app.services.employee_access import apply_scope, employee_scope
from app.services.employee_query import build_employee_query
from app.services.pagination import paginate

router = APIRouter(prefix="/api/employees", tags=["employees"])

Writer = Annotated[User, Depends(require_roles(*permissions.EMPLOYEE_WRITE))]
Reader = Annotated[User, Depends(require_roles(*permissions.EMPLOYEE_READ))]

NOT_FOUND = "Employee not found"


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _load(db: DbSession, employee_id: int) -> Employee:
    employee = db.scalar(
        select(Employee)
        .options(selectinload(Employee.department))
        .where(Employee.id == employee_id)
        # Refresh objects already in the session (e.g. after a department change in this request).
        .execution_options(populate_existing=True)
    )
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return employee


def _check_department(db: DbSession, department_id: int) -> None:
    department = db.get(Department, department_id)
    if department is None:
        raise _invalid("Department does not exist")
    if not department.is_active:
        raise _invalid("Department is inactive")


def _check_unique(db: DbSession, values: dict[str, Any], exclude_id: int | None = None) -> None:
    """Pre-checks for friendly 409 messages; the database unique constraints remain the final guard."""
    checks = (
        ("employee_code", Employee.employee_code, "Employee code is already in use"),
        ("work_email", Employee.work_email, "Work email is already in use"),
    )
    for field, column, message in checks:
        if field in values:
            stmt = select(Employee.id).where(column == values[field])
            if exclude_id is not None:
                stmt = stmt.where(Employee.id != exclude_id)
            if db.scalar(stmt) is not None:
                raise _conflict(message)

    if values.get("user_id") is not None:
        if db.get(User, values["user_id"]) is None:
            raise _invalid("User account does not exist")
        stmt = select(Employee.id).where(Employee.user_id == values["user_id"])
        if exclude_id is not None:
            stmt = stmt.where(Employee.id != exclude_id)
        if db.scalar(stmt) is not None:
            raise _conflict("User account is already linked to another employee")


def _commit(db: DbSession) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict("Employee code, work email or user account is already in use") from None


def _page(db: DbSession, params: EmployeeListParams, user: User) -> Page[EmployeeRead]:
    stmt = apply_scope(build_employee_query(params), employee_scope(db, user))
    items, total = paginate(db, stmt, params)
    return Page[EmployeeRead].build([EmployeeRead.model_validate(e) for e in items], total, params)


@router.post("", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
def create_employee(payload: EmployeeCreate, db: DbSession, actor: Writer) -> Employee:
    values = payload.model_dump()
    _check_department(db, payload.department_id)
    _check_unique(db, values)

    employee = Employee(**values)
    db.add(employee)
    db.flush()
    record_audit(
        db, actor=actor, action="employee.created", entity_type="employee", entity_id=employee.id,
        details={"employee_code": employee.employee_code, "department_id": employee.department_id},
    )
    _commit(db)
    return _load(db, employee.id)


@router.get("", response_model=Page[EmployeeRead])
def list_employees(
    params: Annotated[EmployeeListParams, Query()], db: DbSession, user: Reader
) -> Page[EmployeeRead]:
    """Results are scoped by role: ADMIN/HR see all, MANAGER their department, EMPLOYEE themselves."""
    return _page(db, params, user)


# Declared before /{employee_id} so "search" is never parsed as an id.
@router.get("/search", response_model=Page[EmployeeRead])
def search_employees(
    params: Annotated[EmployeeSearchParams, Query()], db: DbSession, user: Reader
) -> Page[EmployeeRead]:
    """Like the list endpoint, but `q` (2-100 characters) is required. Same role scoping applies."""
    return _page(db, params, user)


@router.get("/{employee_id}", response_model=EmployeeRead)
def get_employee(employee_id: int, db: DbSession, user: Reader) -> Employee:
    employee = _load(db, employee_id)
    # Out-of-scope records look exactly like missing ones, so ids cannot be probed.
    if not employee_scope(db, user).allows(employee):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return employee


@router.patch("/{employee_id}", response_model=EmployeeRead)
def update_employee(
    employee_id: int, payload: EmployeeUpdate, db: DbSession, actor: Writer
) -> Employee:
    employee = _load(db, employee_id)
    changes = payload.model_dump(exclude_unset=True)

    if "department_id" in changes and changes["department_id"] != employee.department_id:
        _check_department(db, changes["department_id"])
    _check_unique(db, changes, exclude_id=employee.id)

    diff = apply_changes(employee, changes)
    if diff:
        record_audit(
            db, actor=actor, action="employee.updated", entity_type="employee",
            entity_id=employee.id, details={"changes": diff},
        )
        _commit(db)
    return _load(db, employee.id)


@router.patch("/{employee_id}/status", response_model=EmployeeRead)
def change_employee_status(
    employee_id: int, payload: EmployeeStatusUpdate, db: DbSession, actor: Writer
) -> Employee:
    employee = _load(db, employee_id)
    diff = apply_changes(employee, {"status": payload.status})
    if diff:
        details: dict[str, Any] = {"changes": diff}
        if payload.reason:
            details["reason"] = payload.reason
        record_audit(
            db, actor=actor, action="employee.status_changed", entity_type="employee",
            entity_id=employee.id, details=details,
        )
        _commit(db)
    return _load(db, employee.id)
