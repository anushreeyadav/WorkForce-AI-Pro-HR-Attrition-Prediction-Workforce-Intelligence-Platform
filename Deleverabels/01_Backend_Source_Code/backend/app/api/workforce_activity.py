"""Workforce activity feed, incremental updates (polling) and monitoring status. Read-only and on demand.

Nothing here is a push or WebSocket stream: clients poll /updates with a signed cursor. Every endpoint is
read-only (no audit rows, notifications or other writes).

Reading never creates or changes records, audit rows or notifications. Every endpoint applies the same scope:
ADMIN/HR organization-wide (or a department), MANAGER pinned to their own department, EMPLOYEE no access.
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import DbSession, require_roles
from app.api.workforce_analytics import resolve_department
from app.core import permissions
from app.core.config import get_settings
from app.models import AlertPriority, User, UserRole
from app.models.base import utcnow
from app.schemas.common import PaginationParams
from app.services import activity_cursor
from app.services import activity_feed as feed
from app.services import alert_sla

router = APIRouter(prefix="/api/workforce-activity", tags=["workforce-activity"])

Reader = Annotated[User, Depends(require_roles(*permissions.ACTIVITY_READ))]
Category = Literal["attendance", "leave", "employee_record", "employee_status", "risk_event", "risk_alert",
                   "risk_escalation", "intervention", "recommendation"]
POLL_NOTE = ("Polling, not a live stream: items appear once they are stored and at least the settle delay old. "
             "Items are oldest first; pass next_cursor back unchanged and de-duplicate by event_id.")
STATUS_NOTE = ("Reflects records stored in the database as of server_time. It is not real-time telemetry, and "
               "risk alerts are review aids based on estimates, not facts about individuals.")
Kind = Literal["OBSERVED_EVENT", "RISK_EVENT"]
# Values used by the sources that carry a priority (alerts, escalations, interventions: LOW-HIGH; recommendations:
# LOW-CRITICAL). Sources without a priority are excluded whenever a priority filter is given.
Priority = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
FEED_NOTE = ("Assembled on demand from stored records when requested; it is not a live stream. recorded_at is when "
             "the platform stored each record; source_event_at is shown only where an independent event time exists.")


class WindowParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = Field(default=None, description="Organization-local date, inclusive")
    date_to: date | None = Field(default=None, description="Organization-local date, inclusive (default today)")
    department_id: Annotated[int, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _order(self) -> "WindowParams":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class FeedParams(PaginationParams, WindowParams):
    category: list[Category] | None = None
    kind: list[Kind] | None = None
    priority: list[Priority] | None = None


class UpdatesParams(WindowParams):
    cursor: str | None = Field(default=None, min_length=1, max_length=activity_cursor.MAX_LENGTH)
    limit: int = Field(default=50, ge=1, le=500, description="Capped by ACTIVITY_UPDATES_MAX_PAGE_SIZE")
    category: list[Category] | None = None
    kind: list[Kind] | None = None
    priority: list[Priority] | None = None


class ScopeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: Annotated[int, Field(gt=0)] | None = None


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _bounds(params: WindowParams, default_days: int) -> tuple[datetime, datetime, date, date]:
    settings = get_settings()
    tz = settings.org_tz
    today = utcnow().astimezone(tz).date()
    end_day = params.date_to or today
    start_day = params.date_from or end_day - timedelta(days=default_days - 1)
    if start_day > end_day:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be on or before date_to")
    if (end_day - start_day).days + 1 > settings.activity_feed_max_window_days:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"The date range may cover at most {settings.activity_feed_max_window_days} days")
    start = datetime.combine(start_day, time.min, tzinfo=tz)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz)
    return start, end, start_day, end_day


def _scope(db: DbSession, user: User, department_id: int | None) -> feed.Scope:
    resolved = resolve_department(db, user, department_id)
    return feed.Scope(department_id=resolved, user_id=user.id,
                      include_organization_items=user.role in (UserRole.ADMIN, UserRole.HR) and resolved is None)


def _filters(params) -> tuple[set[str], set[str] | None, set[str] | None]:
    return set(params.category or feed.SOURCES), set(params.kind or []) or None, set(params.priority or []) or None


def _collect(db: DbSession, user: User, params: FeedParams):
    scope = _scope(db, user, params.department_id)
    start, end, start_day, end_day = _bounds(params, 7)
    categories, kinds, priorities = _filters(params)
    result = feed.collect(db, scope, start, end, categories, kinds, priorities, get_settings().activity_feed_max_items)
    return scope, result, start_day, end_day, end


@router.get("/sources")
def sources(_: Reader) -> dict:
    return {"categories": [{"category": c, "kind": k, "source": s, "timestamp_basis": t} for c, (k, s, t) in feed.SOURCES.items()],
            "signal_kinds": [feed.AGGREGATE_SIGNAL, feed.DATA_QUALITY_SIGNAL],
            "excluded": {"payroll": "Not included: payroll amounts are sensitive and managers have no payroll access",
                         "audit_duplicates": "Audit rows that duplicate a domain record (e.g. leave.approved) are not listed"},
            "note": FEED_NOTE}


@router.get("/feed")
def activity_feed(params: Annotated[FeedParams, Query()], db: DbSession, user: Reader) -> dict:
    scope, result, start_day, end_day, end = _collect(db, user, params)
    page = result.items[params.offset:params.offset + params.page_size]
    total = len(result.items)
    # Position for GET /updates: everything up to the settle cutoff (or the window end) counts as seen. Items newer
    # than the cutoff may be returned again by /updates, so clients de-duplicate by event_id.
    settings = get_settings()
    cutoff = min(utcnow() - timedelta(seconds=settings.activity_updates_settle_seconds), end - timedelta(microseconds=1))
    categories, kinds, priorities = _filters(params)
    updates_cursor = activity_cursor.encode(
        (cutoff, feed.AFTER_ALL_RANK, 0, 0), user.id,
        activity_cursor.fingerprint(scope.department_id, categories, kinds, priorities), settings)
    return {"scope": {"department_id": scope.department_id}, "window": {"from": start_day.isoformat(), "to": end_day.isoformat()},
            "items": [i.as_dict() for i in page], "total": total, "page": params.page, "page_size": params.page_size,
            "pages": (total + params.page_size - 1) // params.page_size, "truncated": result.truncated,
            "truncated_sources": result.sources_truncated, "updates_cursor": updates_cursor, "note": FEED_NOTE}


@router.get("/summary")
def activity_summary(params: Annotated[FeedParams, Query()], db: DbSession, user: Reader) -> dict:
    """Counts over exactly the same scope, window and filters as /feed."""
    scope, result, start_day, end_day, _ = _collect(db, user, params)
    by_category: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for item in result.items:
        by_category[item.category] = by_category.get(item.category, 0) + 1
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        key = f"{item.category}.{item.action}"
        by_action[key] = by_action.get(key, 0) + 1
    return {"scope": {"department_id": scope.department_id}, "window": {"from": start_day.isoformat(), "to": end_day.isoformat()},
            "total": len(result.items), "by_category": by_category, "by_kind": by_kind, "by_action": by_action,
            "truncated": result.truncated, "note": FEED_NOTE}


@router.get("/updates")
def activity_updates(params: Annotated[UpdatesParams, Query()], db: DbSession, user: Reader) -> dict:
    """Items recorded after the cursor, oldest first, in the structured position order (recorded_at, source rank,
    record id, action rank). Without a cursor, starts at date_from (or ACTIVITY_UPDATES_CURSOR_MAX_AGE_HOURS ago)."""
    settings = get_settings()
    now = utcnow()
    scope = _scope(db, user, params.department_id)
    categories, kinds, priorities = _filters(params)
    filters = activity_cursor.fingerprint(scope.department_id, categories, kinds, priorities)
    cutoff = now - timedelta(seconds=settings.activity_updates_settle_seconds)
    if params.date_from or params.date_to:
        start, end, _, _ = _bounds(params, 7)
        lower, upper = start, min(cutoff, end - timedelta(microseconds=1))
    else:
        lower, upper = now - timedelta(hours=settings.activity_updates_cursor_max_age_hours), cutoff
    if params.cursor is None:
        after = (lower, feed.BEFORE_ALL_RANK, 0, 0)
    else:
        try:
            after = activity_cursor.decode(params.cursor, user.id, filters, now, settings)
        except activity_cursor.CursorExpired as exc:
            raise _error(status.HTTP_410_GONE, str(exc)) from None
        except activity_cursor.CursorError as exc:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    limit = min(params.limit, settings.activity_updates_max_page_size)
    result = feed.updates(db, scope, after, lower, upper, categories, kinds, priorities, limit)
    return {"server_time": now.isoformat(), "scope": {"department_id": scope.department_id},
            "items": [item.as_dict() for _, item in result.items], "count": len(result.items), "page_size": limit,
            "has_more": result.has_more, "next_cursor": activity_cursor.encode(result.next_key, user.id, filters, settings),
            "settled_until": upper.isoformat(), "recommended_poll_seconds": settings.activity_poll_interval_seconds,
            "note": POLL_NOTE}


@router.get("/status")
def monitoring_status(params: Annotated[ScopeParams, Query()], db: DbSession, user: Reader) -> dict:
    """Read-only monitoring summary for the caller's scope. Calls no model and writes nothing."""
    settings = get_settings()
    now = utcnow()
    tz = settings.org_tz
    scope = _scope(db, user, params.department_id)

    snapshot = feed.latest_snapshot(db, scope.department_id)
    if snapshot is None:
        snapshot_info = {"status": "insufficient_data", "latest_snapshot_date": None, "captured_at": None, "age_days": None}
    else:
        age = (now.astimezone(tz).date() - snapshot[0]).days
        snapshot_info = {"status": "stale" if age > settings.workforce_snapshot_max_age_days else "available",
                         "latest_snapshot_date": snapshot[0].isoformat(), "captured_at": snapshot[1].isoformat(),
                         "age_days": age}
    snapshot_info["max_age_days"] = settings.workforce_snapshot_max_age_days

    lookback_days = settings.activity_feed_max_window_days
    latest = feed.latest_recorded(db, scope, now - timedelta(days=lookback_days), now + timedelta(microseconds=1))
    activity_info = {"status": "available" if latest else "insufficient_data",
                     "latest_recorded_at": latest.isoformat() if latest else None, "lookback_days": lookback_days}

    policy = alert_sla.SlaPolicy.from_settings(settings)
    by_priority = {p.value: 0 for p in AlertPriority}
    by_sla = {s: 0 for s in alert_sla.SLA_STATES if s != alert_sla.CLOSED}
    for priority, alert_status, first_detected, acknowledged, resolved in feed.active_alert_rows(db, scope):
        by_priority[priority.value] += 1
        by_sla[alert_sla.sla_status(priority, alert_status, first_detected, acknowledged, resolved, now, policy).state] += 1

    today = now.astimezone(tz).date()
    window_start = datetime.combine(today - timedelta(days=6), time.min, tzinfo=tz)
    window_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    counts = feed.category_counts(db, scope, window_start, window_end)
    capped = sorted(c for c, n in counts.items() if n >= settings.activity_feed_max_items)
    return {
        "server_time": now.isoformat(), "scope": {"department_id": scope.department_id},
        "snapshot": snapshot_info, "activity": activity_info,
        "risk_alerts": {"status": "available", "active_total": sum(by_priority.values()), "by_priority": by_priority,
                        "by_sla_state": by_sla},
        "completeness": {
            "default_feed_window": {"from": (today - timedelta(days=6)).isoformat(), "to": today.isoformat()},
            "per_source_cap": settings.activity_feed_max_items,
            "categories_at_or_over_cap": capped, "feed_truncation_possible": bool(capped),
            "known_limitations": [
                "Only recorded events are shown; activity that was never entered is invisible.",
                "Employee status changes are dated by when they were recorded, which may lag the real change.",
                "Records committed more than ACTIVITY_UPDATES_SETTLE_SECONDS after their timestamp can be missed by "
                "an existing /updates cursor; reload /feed periodically.",
                "Deleted or purged source records disappear from the feed without a removal event.",
            ]},
        "polling": {"recommended_interval_seconds": settings.activity_poll_interval_seconds,
                    "max_page_size": settings.activity_updates_max_page_size,
                    "settle_seconds": settings.activity_updates_settle_seconds,
                    "cursor_max_age_hours": settings.activity_updates_cursor_max_age_hours},
        "note": STATUS_NOTE,
    }


@router.get("/signals")
def operational_signals(params: Annotated[WindowParams, Query()], db: DbSession, user: Reader) -> dict:
    scope = _scope(db, user, params.department_id)
    return {"scope": {"department_id": scope.department_id}, "signals": feed.signals(db, get_settings(), scope, utcnow()),
            "note": feed.NOT_A_PREDICTION}


@router.get("/departments/{department_id}")
def department_activity(department_id: int, params: Annotated[WindowParams, Query()], db: DbSession, user: Reader) -> dict:
    if params.department_id is not None and params.department_id != department_id:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "department_id query parameter conflicts with the path")
    scope = _scope(db, user, department_id)
    start, end, _, _ = _bounds(params, 30)
    return {"scope": {"department_id": scope.department_id},
            "activity": feed.department_activity(db, get_settings(), scope.department_id, user.id, start, end),
            "signals": feed.signals(db, get_settings(), scope, utcnow())}
