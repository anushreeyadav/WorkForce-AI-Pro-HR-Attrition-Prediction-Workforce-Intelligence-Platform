"""Audit-log search and compliance export. Read-only over an append-only table: there is no endpoint that edits
or deletes audit rows.

ADMIN/HR: organization-wide (optionally one department). MANAGER: records associated with their own department
only (see app/services/audit_query.py); other departments return 404. EMPLOYEE: 403. Responses use an explicit
schema with sanitized details. Queries are bounded by a date window and pagination; the export by the report row
limits. Each export writes exactly one audit row ("audit.report_exported") with its filters and row count.
"""

import json
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.deps import DbSession, require_roles
from app.api.reports import render_report
from app.api.workforce_analytics import resolve_department
from app.core import permissions
from app.core.config import get_settings
from app.models import User, UserRole
from app.models.base import utcnow
from app.schemas.common import PaginationParams
from app.services import audit_catalog, audit_query
from app.services import reports as report_data
from app.services.audit import record_audit
from app.services.pagination import paginate
from app.services.report_export import Column

router = APIRouter(prefix="/api/audit-logs", tags=["audit-logs"])

Reader = Annotated[User, Depends(require_roles(*permissions.AUDIT_READ))]
Exporter = Annotated[User, Depends(require_roles(*permissions.AUDIT_EXPORT))]
PositiveId = Annotated[int, Field(gt=0)]
DEFAULT_DAYS = 30
NOTE = ("Audit rows are append-only through the application. They are not cryptographically tamper-evident: "
        "privileged database operators can still change stored rows.")


class AuditFilterParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = Field(default=None, description="Organization-local date, inclusive (default: 30 days ago)")
    date_to: date | None = Field(default=None, description="Organization-local date, inclusive (default: today)")
    department_id: PositiveId | None = None
    actor_user_id: PositiveId | None = None
    action: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    sort: Literal["newest", "oldest"] = "newest"

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str | None) -> str | None:
        if value is not None and value not in audit_catalog.ACTIONS:
            raise ValueError("Unknown audit action; see /api/audit-logs/catalog")
        return value

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str | None) -> str | None:
        if value is not None and value not in audit_catalog.CATEGORIES:
            raise ValueError("Unknown audit category; see /api/audit-logs/catalog")
        return value

    @field_validator("entity_type")
    @classmethod
    def _known_entity_type(cls, value: str | None) -> str | None:
        if value is not None and value not in audit_catalog.ENTITY_TYPES:
            raise ValueError("Unknown entity type; see /api/audit-logs/catalog")
        return value

    @model_validator(mode="after")
    def _order(self) -> "AuditFilterParams":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class AuditListParams(PaginationParams, AuditFilterParams):
    pass


class AuditExportParams(AuditFilterParams):
    format: Literal["csv", "xlsx", "pdf"] = "csv"


class AuditLogItem(BaseModel):
    id: int
    created_at: datetime
    actor_user_id: int | None
    actor_hidden: bool = Field(description="True when the actor is outside the viewer's department scope")
    action: str
    category: str
    description: str | None
    entity_type: str
    entity_id: str | None
    details: dict[str, Any] | None = Field(description="Sanitized: sensitive values redacted, free text withheld")


class AuditLogPage(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int
    pages: int
    scope: dict
    window: dict
    note: str = NOTE


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _prepare(db: DbSession, user: User, params: AuditFilterParams):
    settings = get_settings()
    department_id = resolve_department(db, user, params.department_id)
    scope = audit_query.AuditScope(department_id=department_id, viewer_user_id=user.id,
                                   restricted=user.role not in (UserRole.ADMIN, UserRole.HR))
    today = utcnow().astimezone(settings.org_tz).date()
    end_day = params.date_to or today
    start_day = params.date_from or end_day - timedelta(days=DEFAULT_DAYS - 1)
    if start_day > end_day:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be on or before date_to")
    if (end_day - start_day).days + 1 > settings.audit_query_max_range_days:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"The date range may cover at most {settings.audit_query_max_range_days} days")
    tz = settings.org_tz
    filters = audit_query.AuditFilters(
        start=datetime.combine(start_day, time.min, tzinfo=tz),
        end=datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz),
        actor_user_id=params.actor_user_id, action=params.action, category=params.category,
        entity_type=params.entity_type, entity_id=params.entity_id, newest_first=params.sort == "newest")
    return scope, filters, start_day, end_day


@router.get("/catalog")
def audit_catalog_endpoint(user: Reader) -> dict:
    """Known actions, categories and entity types (static; contains no audit data)."""
    restricted = user.role not in (UserRole.ADMIN, UserRole.HR)
    return {
        "actions": [{"action": a, "category": c, "entity_type": e, "description": d,
                     "visible_to_you": not restricted or e in audit_query.DEPARTMENT_LINKED_TYPES}
                    for a, (c, e, d) in audit_catalog.ACTIONS.items()],
        "categories": list(audit_catalog.CATEGORIES), "entity_types": list(audit_catalog.ENTITY_TYPES),
        "max_range_days": get_settings().audit_query_max_range_days, "note": NOTE,
    }


@router.get("", response_model=AuditLogPage)
def list_audit_logs(params: Annotated[AuditListParams, Query()], db: DbSession, user: Reader) -> AuditLogPage:
    scope, filters, start_day, end_day = _prepare(db, user, params)
    rows, total = paginate(db, audit_query.statement(scope, filters), params)
    visible = audit_query.visible_actor_ids(db, scope, {r.actor_user_id for r in rows if r.actor_user_id is not None})
    return AuditLogPage(items=[AuditLogItem(**item) for item in audit_query.present(rows, visible)], total=total,
                        page=params.page, page_size=params.page_size,
                        pages=(total + params.page_size - 1) // params.page_size,
                        scope={"department_id": scope.department_id, "department_linked_only": scope.restricted},
                        window={"from": start_day.isoformat(), "to": end_day.isoformat()})


AUDIT_COLUMNS = [Column("id", "Audit ID", "int"), Column("created_at", "Recorded at", "datetime"),
                 Column("actor_user_id", "Actor user ID", "int"), Column("action", "Action"),
                 Column("category", "Category"), Column("description", "Description"),
                 Column("entity_type", "Entity type"), Column("entity_id", "Entity ID"),
                 Column("details", "Details (sanitized)")]


@router.get("/export")
def export_audit_logs(params: Annotated[AuditExportParams, Query()], db: DbSession, user: Exporter) -> Response:
    """CSV / XLSX / PDF of the same sanitized rows as the list endpoint (ADMIN/HR). Larger than the report row
    limit is refused (422). The export is audited once, after the rows are read, so it never includes itself."""
    scope, filters, start_day, end_day = _prepare(db, user, params)
    settings = get_settings()
    limit = settings.report_pdf_max_rows if params.format == "pdf" else settings.report_max_rows
    rows = db.scalars(audit_query.statement(scope, filters).limit(limit + 1)).all()
    if len(rows) > limit:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"The export has more than {limit} rows for this format; narrow the filters")
    items = audit_query.present(list(rows), None)
    for item in items:
        item["details"] = json.dumps(item["details"], sort_keys=True, default=str) if item["details"] is not None else None
    data = report_data.ReportData(AUDIT_COLUMNS, items)
    applied = {k: v for k, v in {"actor_user_id": params.actor_user_id, "action": params.action,
                                 "category": params.category, "entity_type": params.entity_type,
                                 "entity_id": params.entity_id, "department_id": scope.department_id}.items()
               if v is not None}
    label = ", ".join(f"{k}={v}" for k, v in applied.items()) or "none"
    response = render_report("audit-log", "Audit log", params.format, data,
                             ["organization" if scope.department_id is None else f"department-{scope.department_id}",
                              f"{start_day}_{end_day}"],
                             [f"Recorded {start_day.isoformat()} to {end_day.isoformat()}; filters: {label}; "
                              f"order: {params.sort}", NOTE])
    record_audit(db, actor=user, action="audit.report_exported", entity_type="audit_log", entity_id=None,
                 details={"format": params.format, "rows": len(items), "date_from": start_day.isoformat(),
                          "date_to": end_day.isoformat(), "filters": applied, "sort": params.sort})
    db.commit()
    return response
