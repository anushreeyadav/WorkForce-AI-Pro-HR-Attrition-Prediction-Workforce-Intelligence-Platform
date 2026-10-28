"""Workforce analytics: snapshots, overview, trends, forecast, stability and retention (aggregates only).

Observed metrics, stored AI risk indicators and future estimates are returned in separate, labelled blocks.
Nothing here changes employee, attendance, leave or payroll data, trains or calls a model.
"""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import Department, User, UserRole, WorkforceSnapshot
from app.models.base import utcnow
from app.schemas.common import Page
from app.schemas.workforce import (
    ESTIMATE_NOTE,
    OBSERVED_NOTE,
    CaptureRequest,
    DepartmentDetailParams,
    Envelope,
    ForecastParams,
    RetentionParams,
    ScopeParams,
    SnapshotListParams,
    SnapshotRead,
    StabilityParams,
    TrendParams,
)
from app.services import workforce_analytics as service
from app.services import workforce_math as wm
from app.services.audit import record_audit
from app.services.employee_access import employee_scope
from app.services.pagination import paginate

router = APIRouter(prefix="/api/workforce-analytics", tags=["workforce-analytics"])

SnapshotAdmin = Annotated[User, Depends(require_roles(*permissions.WORKFORCE_SNAPSHOT_ADMIN))]
Reader = Annotated[User, Depends(require_roles(*permissions.WORKFORCE_ANALYTICS_READ))]

MAX_TREND_MONTHS = 60
DEPARTMENT_NOT_FOUND = "Department not found"


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _today() -> date:
    return utcnow().astimezone(get_settings().org_tz).date()


def resolve_department(db: DbSession, user: User, requested: int | None) -> int | None:
    """None = organization scope (ADMIN/HR only). Managers are pinned to their own department; any other
    department looks exactly like a missing one (404), so existence never leaks."""
    if user.role == UserRole.MANAGER:
        own = employee_scope(db, user).department_id
        if own is None:
            raise _error(status.HTTP_403_FORBIDDEN, "Your account is not linked to a department")
        if requested is not None and requested != own:
            raise _error(status.HTTP_404_NOT_FOUND, DEPARTMENT_NOT_FOUND)
        return own
    if requested is not None and db.get(Department, requested) is None:
        raise _error(status.HTTP_404_NOT_FOUND, DEPARTMENT_NOT_FOUND)
    return requested


def _scope(department_id: int | None) -> dict:
    return {"level": "organization" if department_id is None else "department", "department_id": department_id}


# --- Snapshots ---------------------------------------------------------------------------------


@router.post("/snapshots/capture", response_model=SnapshotRead, status_code=status.HTTP_201_CREATED)
def capture_snapshot(payload: CaptureRequest, db: DbSession, actor: SnapshotAdmin) -> WorkforceSnapshot:
    """Capture today's aggregates from live records. Snapshots are immutable: a second capture for the same
    date is refused (409). Past or future dates are refused because current records cannot describe them."""
    settings = get_settings()
    today = _today()
    if payload.snapshot_date is not None and payload.snapshot_date != today:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"Snapshots reflect current records and can only be captured for today ({today.isoformat()})")
    try:
        snapshot = service.capture_snapshot(db, actor, utcnow(), settings.org_tz)
        record_audit(db, actor=actor, action="workforce.snapshot_captured", entity_type="workforce_snapshot",
                     entity_id=snapshot.id, details={"snapshot_date": snapshot.snapshot_date.isoformat(),
                                                     "total_employees": snapshot.total_employees,
                                                     "departments": len(snapshot.departments)})
        db.commit()
    except service.SnapshotExists as exc:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, f"A snapshot for {today.isoformat()} already exists (id {exc.snapshot_id}); snapshots are immutable") from None
    except IntegrityError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, f"A snapshot for {today.isoformat()} was captured concurrently") from None
    return db.scalar(select(WorkforceSnapshot).options(selectinload(WorkforceSnapshot.departments))
                     .where(WorkforceSnapshot.id == snapshot.id).execution_options(populate_existing=True))


@router.get("/snapshots", response_model=Page[SnapshotRead])
def list_snapshots(params: Annotated[SnapshotListParams, Query()], db: DbSession, _: SnapshotAdmin) -> Page[SnapshotRead]:
    stmt = select(WorkforceSnapshot).options(selectinload(WorkforceSnapshot.departments))
    if params.date_from:
        stmt = stmt.where(WorkforceSnapshot.snapshot_date >= params.date_from)
    if params.date_to:
        stmt = stmt.where(WorkforceSnapshot.snapshot_date <= params.date_to)
    items, total = paginate(db, stmt.order_by(WorkforceSnapshot.snapshot_date.desc()), params)
    return Page[SnapshotRead].build([SnapshotRead.model_validate(s) for s in items], total, params)


# --- Builders (shared by endpoints and the department detail) -------------------------------------


def _overview(db: DbSession, department_id: int | None) -> dict:
    settings = get_settings()
    today = _today()
    overall, per_department = service.current_counts(db, department_id)
    names = dict(db.execute(select(Department.id, Department.name)).all())
    transitions = service.status_transitions(db, today - timedelta(days=89), today, settings.org_tz, department_id)
    obs = service.observations(db, department_id)
    unknown = service.inactive_without_recorded_departure(db, department_id)
    return {
        "as_of": today.isoformat(),
        "current": overall,
        "by_department": [{"department_id": d, "department_name": names.get(d), **c} for d, c in sorted(per_department.items())],
        "joiners_last_90_days": len(service.joiners(db, today - timedelta(days=89), today, department_id)),
        "recorded_departures_last_90_days": sum(1 for t in transitions if t.kind == "departure"),
        "snapshots": {"count": len(obs), "latest_snapshot_date": obs[-1].snapshot_date.isoformat() if obs else None},
        "data_quality": {"inactive_without_recorded_departure": unknown},
    }


def _month_last_day(idx: int) -> date:
    return date((idx + 1) // 12, (idx + 1) % 12 + 1, 1) - timedelta(days=1)


def _month_rows(db: DbSession, department_id: int | None, start: date, end: date) -> list[dict]:
    """One row per calendar month touched by [start, end]. Joiners and departures always cover the WHOLE month
    (so a mid-month date_from/date_to never produces a partial month under a full-month label), but never days after
    today: future-dated joining dates are not observed events."""
    settings = get_settings()
    today = _today()
    first, last = wm.month_index(start.year, start.month), wm.month_index(end.year, end.month)
    observed = service.month_end(service.observations(db, department_id))
    flow_start, flow_end = date(first // 12, first % 12 + 1, 1), min(_month_last_day(last), today)
    joined = service.joiners(db, flow_start, flow_end, department_id) if flow_start <= flow_end else []
    departed = ([t for t in service.status_transitions(db, flow_start, flow_end, settings.org_tz, department_id)
                 if t.kind == "departure"] if flow_start <= flow_end else [])
    rows = []
    for idx in range(first, last + 1):
        obs = observed.get(idx)
        prev = observed.get(idx - 1)
        departures = sum(1 for t in departed if wm.month_index(t.recorded_on.year, t.recorded_on.month) == idx)
        rate = (wm.turnover_rate(departures, [prev.active, obs.active]) if obs and prev else None)
        rows.append({
            "month": wm.index_to_month(idx), "observed": obs is not None,
            "snapshot_date": obs.snapshot_date.isoformat() if obs else None,
            "total_employees": obs.total if obs else None, "active_employees": obs.active if obs else None,
            "on_leave_employees": obs.on_leave if obs else None, "inactive_employees": obs.inactive if obs else None,
            "joiners": sum(1 for d, _ in joined if wm.month_index(d.year, d.month) == idx),
            "recorded_departures": departures,
            "turnover_rate": round(rate, 4) if rate is not None else None,
            "month_complete": _month_last_day(idx) < today,
        })
    return rows


def _years(rows: list[dict]) -> list[dict]:
    years: dict[str, list[dict]] = {}
    for row in rows:
        years.setdefault(row["month"][:4], []).append(row)
    result = []
    for year, months in sorted(years.items()):
        observed = [m for m in months if m["observed"]]
        departures = sum(m["recorded_departures"] for m in months)
        rate = wm.turnover_rate(departures, [m["active_employees"] for m in observed]) if len(observed) >= 2 else None
        result.append({"year": int(year), "months_in_range": len(months), "months_observed": len(observed),
                       "year_end_active_employees": observed[-1]["active_employees"] if observed else None,
                       "joiners": sum(m["joiners"] for m in months), "recorded_departures": departures,
                       "turnover_rate": round(rate, 4) if rate is not None else None})
    return result


def _default_range(params, months: int) -> tuple[date, date]:
    """Explicit dates, or the last `months` calendar months up to today."""
    end = params.date_to or _today()
    if params.date_from is not None:
        start = params.date_from
    else:
        idx = wm.month_index(end.year, end.month) - (months - 1)
        start = date(idx // 12, idx % 12 + 1, 1)
    if start > end:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be on or before date_to")
    return start, end


def _forecast(db: DbSession, department_id: int | None, horizon: int) -> dict:
    settings = get_settings()
    if horizon > settings.workforce_forecast_max_horizon_months:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     f"horizon_months may be at most {settings.workforce_forecast_max_horizon_months}")
    all_obs = service.observations(db, department_id)
    observed = service.month_end(all_obs)
    points = sorted((idx, obs.active) for idx, obs in observed.items())
    latest = all_obs[-1].snapshot_date if all_obs else None
    result = wm.forecast(points, points[-1][0] if points else None, horizon, settings.workforce_forecast_min_months,
                         latest_age_days=(_today() - latest).days if latest else None,
                         max_age_days=settings.workforce_snapshot_max_age_days)
    history = [{"month": wm.index_to_month(idx), "observed_active_employees": value} for idx, value in points]
    projected_change = (result.projections[-1]["projected_active_headcount"] - points[-1][1]) if result.projections else None
    return {
        "status": result.status, "reason": result.reason, "method": "linear_trend_ols_month_end_active_headcount",
        "latest_snapshot_date": latest.isoformat() if latest else None,
        "max_snapshot_age_days": settings.workforce_snapshot_max_age_days,
        "missing_months_in_data_window": result.missing_months,
        "observed_months": result.observed_months, "required_months": result.required_months,
        "data_window": {"first_month": history[0]["month"] if history else None,
                        "last_month": history[-1]["month"] if history else None},
        "horizon_months": horizon, "observed": history, "projections": result.projections,
        "projected_net_change": projected_change, "monthly_trend": result.monthly_trend,
        "backtest_mean_absolute_error": result.backtest_mae, "backtest_note": result.backtest_note,
        "rounding": "Projections are rounded half-up to whole people and never below 0",
        "note": ESTIMATE_NOTE,
    }


def _stability(db: DbSession, department_id: int | None) -> dict:
    return service.stability_report(db, get_settings(), department_id, _today())


def _retention(db: DbSession, user: User, department_id: int | None, start: date, end: date) -> dict:
    settings = get_settings()
    flow_end = min(end, _today())  # future-dated joining dates are not observed events
    transitions = service.status_transitions(db, start, flow_end, settings.org_tz, department_id) if start <= flow_end else []
    departures = sum(1 for t in transitions if t.kind == "departure")
    in_window = service.observations(db, department_id, start, end)
    rate = wm.turnover_rate(departures, [o.active for o in in_window]) if len(in_window) >= 2 else None
    rows = _month_rows(db, department_id, start, end)
    return {
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "observed": {
            "joiners": len(service.joiners(db, start, flow_end, department_id)) if start <= flow_end else 0,
            "recorded_departures": departures,
            "reactivations": sum(1 for t in transitions if t.kind == "reactivation"),
            "turnover_rate": round(rate, 4) if rate is not None else None,
            "turnover_denominator": {"snapshots_in_window": len(in_window),
                                     "average_active_headcount": round(sum(o.active for o in in_window) / len(in_window), 2) if in_window else None},
            "turnover_status": "ok" if rate is not None else "unavailable",
            "turnover_reason": None if rate is not None else "At least 2 snapshots inside the window are needed for a headcount denominator",
            "monthly": rows, "yearly": _years(rows),
            "inactive_without_recorded_departure": service.inactive_without_recorded_departure(db, department_id),
            "note": OBSERVED_NOTE,
        },
        "ai_risk_indicators": service.risk_indicators(db, department_id, user.id),
    }


DEFINITIONS = {
    "recorded_departure": service.DEPARTURE_DEFINITION,
    "joiner": service.JOINER_DEFINITION,
    "turnover_rate": "recorded departures in the period / average active headcount of the snapshots used as denominator",
    "month_observation": "the last snapshot captured in a calendar month; months without a snapshot are not observed",
}


# --- Endpoints ---------------------------------------------------------------------------------


@router.get("/overview", response_model=Envelope)
def overview(params: Annotated[ScopeParams, Query()], db: DbSession, user: Reader) -> Envelope:
    department_id = resolve_department(db, user, params.department_id)
    return Envelope(scope=_scope(department_id), data=_overview(db, department_id), definitions=DEFINITIONS)


@router.get("/trends", response_model=Envelope)
def trends(params: Annotated[TrendParams, Query()], db: DbSession, user: Reader) -> Envelope:
    department_id = resolve_department(db, user, params.department_id)
    start, end = _default_range(params, 24)
    span = wm.month_index(end.year, end.month) - wm.month_index(start.year, start.month) + 1
    if span > MAX_TREND_MONTHS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The date range may cover at most {MAX_TREND_MONTHS} months")
    rows = _month_rows(db, department_id, start, end)
    return Envelope(scope=_scope(department_id), definitions=DEFINITIONS,
                    data={"window": {"from": start.isoformat(), "to": end.isoformat()}, "monthly": rows,
                          "yearly": _years(rows), "note": OBSERVED_NOTE})


@router.get("/forecast", response_model=Envelope)
def forecast(params: Annotated[ForecastParams, Query()], db: DbSession, user: Reader) -> Envelope:
    department_id = resolve_department(db, user, params.department_id)
    return Envelope(scope=_scope(department_id), data=_forecast(db, department_id, params.horizon_months))


@router.get("/stability", response_model=Envelope)
def stability(params: Annotated[StabilityParams, Query()], db: DbSession, user: Reader) -> Envelope:
    """The heuristic stability indicator for your scope. With by_department=true, also one entry per department in
    scope (ADMIN/HR: every department; MANAGER: their own), computed exactly as for that department alone."""
    department_id = resolve_department(db, user, params.department_id)
    data = _stability(db, department_id)
    if params.by_department:
        departments = db.execute(select(Department.id, Department.name, Department.is_active).where(
            Department.id == department_id if department_id is not None else Department.id.is_not(None))
            .order_by(Department.name, Department.id)).all()
        results = service.stability_by_department(db, get_settings(), _today(), [d.id for d in departments])
        data["departments"] = [
            {"department_id": d.id, "department_name": d.name, "is_active": d.is_active,
             **{k: results[d.id][k] for k in ("status", "reason_code", "reason", "score", "components", "metrics",
                                              "latest_snapshot_date", "recorded_departures_in_window")}}
            for d in departments]
    return Envelope(scope=_scope(department_id), data=data, definitions=DEFINITIONS)


@router.get("/retention", response_model=Envelope)
def retention(params: Annotated[RetentionParams, Query()], db: DbSession, user: Reader) -> Envelope:
    department_id = resolve_department(db, user, params.department_id)
    start, end = _default_range(params, 12)
    span = wm.month_index(end.year, end.month) - wm.month_index(start.year, start.month) + 1
    if span > MAX_TREND_MONTHS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The date range may cover at most {MAX_TREND_MONTHS} months")
    return Envelope(scope=_scope(department_id), data=_retention(db, user, department_id, start, end), definitions=DEFINITIONS)


@router.get("/departments/{department_id}", response_model=Envelope)
def department_detail(department_id: int, params: Annotated[DepartmentDetailParams, Query()], db: DbSession, user: Reader) -> Envelope:
    scoped = resolve_department(db, user, department_id)
    end = _today()
    idx = wm.month_index(end.year, end.month) - 11
    start = date(idx // 12, idx % 12 + 1, 1)
    return Envelope(scope=_scope(scoped), definitions=DEFINITIONS, data={
        "overview": _overview(db, scoped), "forecast": _forecast(db, scoped, params.horizon_months),
        "stability": _stability(db, scoped), "retention": _retention(db, user, scoped, start, end)})
