"""Builds employee list/search queries from validated parameters. All values are bound parameters."""

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import selectinload

from app.models import Employee, User
from app.schemas.employee import EmployeeListParams
from app.services.search import LIKE_ESCAPE, contains_pattern

SORT_COLUMNS = {
    "full_name": Employee.full_name,
    "employee_code": Employee.employee_code,
    "date_of_joining": Employee.date_of_joining,
    "created_at": Employee.created_at,
}


def build_employee_query(params: EmployeeListParams) -> Select:
    stmt = select(Employee).options(selectinload(Employee.department))

    if params.q:
        pattern = contains_pattern(params.q)
        stmt = stmt.where(
            or_(
                *(
                    column.ilike(pattern, escape=LIKE_ESCAPE)
                    for column in (
                        Employee.employee_code,
                        Employee.full_name,
                        Employee.work_email,
                        Employee.designation,
                    )
                )
            )
        )
    if params.department_id is not None:
        stmt = stmt.where(Employee.department_id == params.department_id)
    if params.status is not None:
        stmt = stmt.where(Employee.status == params.status)
    if params.designation is not None:
        stmt = stmt.where(func.lower(Employee.designation) == params.designation.lower())
    if params.joined_from is not None:
        stmt = stmt.where(Employee.date_of_joining >= params.joined_from)
    if params.joined_to is not None:
        stmt = stmt.where(Employee.date_of_joining <= params.joined_to)
    if params.user_active is not None:
        stmt = stmt.join(User, Employee.user_id == User.id).where(User.is_active == params.user_active)

    column = SORT_COLUMNS[params.sort_by]
    ordering = column.desc() if params.sort_order == "desc" else column.asc()
    # Employee.id as a tiebreaker keeps pagination stable when sort values repeat.
    return stmt.order_by(ordering, Employee.id.asc())
