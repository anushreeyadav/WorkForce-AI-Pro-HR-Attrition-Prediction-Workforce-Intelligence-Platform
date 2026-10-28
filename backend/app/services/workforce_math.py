"""Pure, deterministic workforce arithmetic (no database). Missing values stay None, never 0.

Forecast: ordinary least-squares linear trend of month-end ACTIVE headcount against month index, fitted on
observed months only (gaps are skipped, never interpolated, and counted in `missing_months`). Projections are
rounded half-up to whole people and floored at 0. A hold-out backtest (last 3 observed months) is reported only
when enough genuine observations exist; otherwise no accuracy figure is given. When the latest observation is
older than the freshness limit the forecast is refused ("stale_data"): projecting from an old month-end would
present months that have already passed as estimates.

Stability indicator (0-100, higher = more stable), a documented heuristic, not a validated measure:
    turnover_component   = 100 x max(0, 1 - annualized_turnover / 0.30)
    net_change_component = 100 x max(0, 1 - |net_change_rate| / 0.20)
    volatility_component = 100 x max(0, 1 - coefficient_of_variation / 0.10)
    score = 0.5 x turnover + 0.25 x net_change + 0.25 x volatility, rounded to 1 decimal
It is produced only when every component is available, the data is recent and the population is large enough
(average active headcount >= min_average_headcount); otherwise status/reason_code explain why and score is None.
Missing information never becomes a favourable score.
"""

import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

BACKTEST_HOLDOUT = 3
TURNOVER_ZERO_AT = 0.30
NET_CHANGE_ZERO_AT = 0.20
VOLATILITY_ZERO_AT = 0.10
WEIGHTS = {"turnover": 0.5, "net_change": 0.25, "volatility": 0.25}


def month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def index_to_month(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def round_people(value: float) -> int:
    return max(0, int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


@dataclass(frozen=True)
class Trend:
    slope: float
    intercept: float

    def at(self, t: int) -> float:
        return self.intercept + self.slope * t


def fit_trend(points: list[tuple[int, float]]) -> Trend:
    """OLS on (t, y). With a single distinct t the slope is 0 (flat at the mean)."""
    n = len(points)
    mean_t = sum(t for t, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    var_t = sum((t - mean_t) ** 2 for t, _ in points)
    slope = 0.0 if var_t == 0 else sum((t - mean_t) * (y - mean_y) for t, y in points) / var_t
    return Trend(slope=slope, intercept=mean_y - slope * mean_t)


@dataclass(frozen=True)
class Forecast:
    status: str  # "ok" | "insufficient_history" | "stale_data"
    observed_months: int
    required_months: int
    projections: list[dict]
    monthly_trend: float | None
    backtest_mae: float | None
    backtest_note: str
    missing_months: int = 0  # unobserved months between the first and last observed month (never filled)
    reason: str | None = None


def missing_months(points: list[tuple[int, int]]) -> int:
    """Calendar months between the first and last observation that have no observation."""
    if not points:
        return 0
    indexes = {t for t, _ in points}
    return max(indexes) - min(indexes) + 1 - len(indexes)


def forecast(points: list[tuple[int, int]], last_observed: int | None, horizon: int, min_months: int,
             latest_age_days: int | None = None, max_age_days: int | None = None) -> Forecast:
    """points: (month_index, month-end active headcount) for observed months only, one per month.

    latest_age_days/max_age_days: age of the latest snapshot used and the freshness limit (both optional)."""
    if len({t for t, _ in points}) != len(points):
        raise ValueError("One observation per month is required")
    points = sorted(points)
    gaps = missing_months(points)
    if len(points) < min_months or last_observed is None:
        reason = f"At least {min_months} observed months are required"
        return Forecast("insufficient_history", len(points), min_months, [], None, None, reason, gaps, reason)
    if max_age_days is not None and (latest_age_days is None or latest_age_days > max_age_days):
        reason = (f"The latest snapshot is older than {max_age_days} days; capture a current snapshot before "
                  "projecting forward")
        return Forecast("stale_data", len(points), min_months, [], None, None, "No backtest: the data is stale",
                        gaps, reason)
    trend = fit_trend([(t, float(y)) for t, y in points])
    projections = [{"month": index_to_month(last_observed + k), "projected_active_headcount": round_people(trend.at(last_observed + k))}
                   for k in range(1, horizon + 1)]

    mae, note = None, f"No backtest: needs at least {min_months + BACKTEST_HOLDOUT} observed months"
    if len(points) >= min_months + BACKTEST_HOLDOUT:
        train, held_out = points[:-BACKTEST_HOLDOUT], points[-BACKTEST_HOLDOUT:]
        backtest_trend = fit_trend([(t, float(y)) for t, y in train])
        mae = round(sum(abs(round_people(backtest_trend.at(t)) - y) for t, y in held_out) / BACKTEST_HOLDOUT, 2)
        note = (f"Mean absolute error over the last {BACKTEST_HOLDOUT} observed months when fitted on earlier months "
                "only; past error does not guarantee future accuracy")
    return Forecast("ok", len(points), min_months, projections, round(trend.slope, 4), mae, note, gaps)


def turnover_rate(departures: int, headcounts: list[int]) -> float | None:
    """departures / average headcount; None when there is no positive denominator."""
    if not headcounts:
        return None
    average = sum(headcounts) / len(headcounts)
    return None if average <= 0 else departures / average


@dataclass(frozen=True)
class StabilityInputs:
    observations: list[tuple[date, int]]  # (snapshot date, active headcount), sorted, within the window
    recorded_departures: int | None  # between first and last observation; None if not measurable
    latest_age_days: int | None
    max_age_days: int
    min_average_headcount: int = 1  # see Settings.workforce_stability_min_average_headcount


def _not_scored(status: str, code: str, reason: str, metrics: dict, missing: list[str]) -> dict:
    return {"status": status, "score": None, "metrics": metrics, "components": None, "missing_metrics": missing,
            "reason": reason, "reason_code": code}


def stability(inputs: StabilityInputs) -> dict:
    obs = inputs.observations
    metrics = {"observations": len(obs), "net_change_rate": None, "coefficient_of_variation": None,
               "annualized_turnover": None, "span_days": None,
               "average_active_headcount": round(sum(v for _, v in obs) / len(obs), 2) if obs else None}
    missing = []
    if len(obs) >= 2:
        first, last = obs[0], obs[-1]
        span = (last[0] - first[0]).days
        metrics["span_days"] = span
        if first[1] > 0:
            metrics["net_change_rate"] = round((last[1] - first[1]) / first[1], 4)
        mean = sum(v for _, v in obs) / len(obs)
        if mean > 0:
            std = math.sqrt(sum((v - mean) ** 2 for _, v in obs) / len(obs))
            metrics["coefficient_of_variation"] = round(std / mean, 4)
        rate = turnover_rate(inputs.recorded_departures, [v for _, v in obs]) if inputs.recorded_departures is not None else None
        if rate is not None and span >= 28:
            metrics["annualized_turnover"] = round(rate * 365 / span, 4)
    for name in ("net_change_rate", "coefficient_of_variation", "annualized_turnover"):
        if metrics[name] is None:
            missing.append(name)

    if len(obs) < 3:
        return _not_scored("insufficient_data", "too_few_snapshots", "At least 3 snapshots in the window are required",
                           metrics, missing)
    if inputs.latest_age_days is None or inputs.latest_age_days > inputs.max_age_days:
        return _not_scored("stale_data", "stale_snapshots", f"The latest snapshot is older than {inputs.max_age_days} days",
                           metrics, missing)
    if metrics["average_active_headcount"] < inputs.min_average_headcount:
        return _not_scored("insufficient_data", "small_population",
                           f"Average active headcount is below {inputs.min_average_headcount}; a single joiner or leaver "
                           "would dominate the indicator", metrics, missing)
    if missing:
        return _not_scored("insufficient_data", "missing_metrics", "Some required metrics are unavailable", metrics, missing)

    components = {
        "turnover": round(100 * max(0.0, 1 - metrics["annualized_turnover"] / TURNOVER_ZERO_AT), 1),
        "net_change": round(100 * max(0.0, 1 - abs(metrics["net_change_rate"]) / NET_CHANGE_ZERO_AT), 1),
        "volatility": round(100 * max(0.0, 1 - metrics["coefficient_of_variation"] / VOLATILITY_ZERO_AT), 1),
    }
    score = round(sum(components[k] * w for k, w in WEIGHTS.items()), 1)
    return {"status": "ok", "score": score, "metrics": metrics, "components": components, "missing_metrics": [],
            "reason": None, "reason_code": None}
