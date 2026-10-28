"""Global search: GET /api/search. Read-only; every record type is filtered by its module's own visibility rules.

Types the caller has no access to are reported in `unavailable_types` and never queried. Inaccessible records are
never returned, even when their labels match.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUser, DbSession
from app.schemas.common import trimmed
from app.services import global_search

router = APIRouter(prefix="/api/search", tags=["search"])

SearchType = Literal["employee", "department", "task", "intervention", "recommendation", "goal", "review_cycle",
                     "performance_review"]
NOTE = ("Matches labels only (names, codes and titles), never descriptions, notes or review content. Payroll, audit "
        "logs, risk alerts, attendance, leave and datasets are not searched here; use their module endpoints.")


class SearchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: trimmed(2, 100) = Field(description="Case-insensitive substring; %, _ and ! match literally")
    types: list[SearchType] | None = Field(default=None, description="Default: every type you may search")
    limit_per_type: Annotated[int, Field(ge=1, le=20)] = 5


class SearchItem(BaseModel):
    type: SearchType
    id: int
    label: str
    summary: dict[str, Any]
    timestamp: datetime | None
    resource: str = Field(description="Detail endpoint (applies its own access rules)")


class SearchResponse(BaseModel):
    query: str
    results: list[SearchItem]
    counts: dict[str, int]
    truncated_types: list[str] = Field(description="Types with more matches than limit_per_type")
    searched_types: list[str]
    unavailable_types: list[str] = Field(description="Requested types your role cannot search")
    note: str = NOTE


@router.get("", response_model=SearchResponse)
def search(params: Annotated[SearchParams, Query()], db: DbSession, user: CurrentUser) -> SearchResponse:
    """Ordered by record type, then match quality (exact, prefix, substring), then label, then id."""
    result = global_search.search(db, user, params.q, set(params.types) if params.types else None, params.limit_per_type)
    return SearchResponse(query=params.q, results=[SearchItem(**item) for item in result.items], counts=result.counts,
                          truncated_types=result.truncated_types, searched_types=result.searched_types,
                          unavailable_types=result.unavailable_types)
