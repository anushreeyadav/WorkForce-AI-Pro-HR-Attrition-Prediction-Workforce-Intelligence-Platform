from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.models import Department, Employee, User, UserRole
from app.schemas.common import Page
from app.schemas.department import (
    DepartmentCreate,
    DepartmentListParams,
    DepartmentRead,
    DepartmentUpdate,
)
from app.schemas.employee import DepartmentEmployeeParams, EmployeeRead
from app.services.audit import apply_changes, record_audit
from app.services.search import keyword_condition
from app.services.employee_access import employee_scope
from app.services.pagination import paginate

router = APIRouter(prefix="/api/departments", tags=["departments"])

DUPLICATE_NAME = "A department with this name already exists"

# Employees per department, computed in one grouped subquery (no per-department queries).
_employee_counts = (
    select(Employee.department_id, func.count(Employee.id).label("employee_count"))
    .group_by(Employee.department_id)
    .subquery()
)


def _with_counts():
    return select(Department, func.coalesce(_employee_counts.c.employee_count, 0)).outerjoin(
        _employee_counts, _employee_counts.c.department_id == Department.id
    )


def _to_read(department: Department, employee_count: int) -> DepartmentRead:
    return DepartmentRead(
        id=department.id,
        name=department.name,
        description=department.description,
        is_active=department.is_active,
        employee_count=employee_count,
        created_at=department.created_at,
        updated_at=department.updated_at,
    )


def _read_one(db: DbSession, department_id: int) -> DepartmentRead:
    row = db.execute(_with_counts().where(Department.id == department_id)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return _to_read(*row)


def _get_or_404(db: DbSession, department_id: int) -> Department:
    department = db.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return department


def _name_taken(db: DbSession, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(Department.id).where(func.lower(Department.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Department.id != exclude_id)
    return db.scalar(stmt) is not None


def _commit_or_conflict(db: DbSession) -> None:
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent request on the unique name constraint.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME) from None


@router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    db: DbSession,
    actor: Annotated[User, Depends(require_roles(*permissions.DEPARTMENT_WRITE))],
) -> DepartmentRead:
    if _name_taken(db, payload.name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME)

    department = Department(**payload.model_dump())
    db.add(department)
    db.flush()
    record_audit(
        db, actor=actor, action="department.created", entity_type="department",
        entity_id=department.id, details={"name": department.name},
    )
    _commit_or_conflict(db)
    return _to_read(department, 0)


@router.get("", response_model=Page[DepartmentRead])
def list_departments(
    params: Annotated[DepartmentListParams, Query()],
    db: DbSession,
    _: Annotated[User, Depends(require_roles(*permissions.DEPARTMENT_READ))],
) -> Page[DepartmentRead]:
    filters = [] if params.is_active is None else [Department.is_active == params.is_active]
    if params.q:
        filters.append(keyword_condition(params.q, Department.name))

    total = db.scalar(select(func.count(Department.id)).where(*filters)) or 0
    rows = db.execute(
        _with_counts()
        .where(*filters)
        .order_by(Department.name.asc(), Department.id.asc())
        .limit(params.page_size)
        .offset(params.offset)
    ).all()
    return Page[DepartmentRead].build([_to_read(*row) for row in rows], total, params)


@router.get("/{department_id}", response_model=DepartmentRead)
def get_department(
    department_id: int,
    db: DbSession,
    _: Annotated[User, Depends(require_roles(*permissions.DEPARTMENT_READ))],
) -> DepartmentRead:
    return _read_one(db, department_id)


@router.patch("/{department_id}", response_model=DepartmentRead)
def update_department(
    department_id: int,
    payload: DepartmentUpdate,
    db: DbSession,
    actor: Annotated[User, Depends(require_roles(*permissions.DEPARTMENT_WRITE))],
) -> DepartmentRead:
    department = _get_or_404(db, department_id)
    changes = payload.model_dump(exclude_unset=True)

    if "name" in changes and _name_taken(db, changes["name"], exclude_id=department.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME)

    diff = apply_changes(department, changes)
    if diff:
        record_audit(
            db, actor=actor, action="department.updated", entity_type="department",
            entity_id=department.id, details={"changes": diff},
        )
        _commit_or_conflict(db)
    return _read_one(db, department.id)


@router.get("/{department_id}/employees", response_model=Page[EmployeeRead])
def list_department_employees(
    department_id: int,
    params: Annotated[DepartmentEmployeeParams, Query()],
    db: DbSession,
    user: Annotated[User, Depends(require_roles(*permissions.DEPARTMENT_MEMBERS_READ))],
) -> Page[EmployeeRead]:
    _get_or_404(db, department_id)

    if user.role == UserRole.MANAGER and employee_scope(db, user).department_id != department_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Managers can only view employees in their own department",
        )

    stmt = select(Employee).options(selectinload(Employee.department)).where(
        Employee.department_id == department_id
    )
    if params.status is not None:
        stmt = stmt.where(Employee.status == params.status)
    stmt = stmt.order_by(Employee.full_name.asc(), Employee.id.asc())

    items, total = paginate(db, stmt, params)
    return Page[EmployeeRead].build([EmployeeRead.model_validate(e) for e in items], total, params)
