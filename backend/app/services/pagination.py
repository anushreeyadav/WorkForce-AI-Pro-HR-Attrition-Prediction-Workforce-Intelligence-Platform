from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.schemas.common import PaginationParams


def paginate(db: Session, stmt: Select, params: PaginationParams) -> tuple[list[Any], int]:
    """Run one COUNT query and one page query for an ORM select (two statements regardless of size)."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = list(db.scalars(stmt.limit(params.page_size).offset(params.offset)).all())
    return items, total
