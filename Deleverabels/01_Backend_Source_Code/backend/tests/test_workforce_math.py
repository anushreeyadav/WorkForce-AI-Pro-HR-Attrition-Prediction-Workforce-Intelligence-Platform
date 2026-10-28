"""Pure workforce arithmetic. All series are synthetic."""

from datetime import date, timedelta

import pytest

from app.services import workforce_math as wm

BASE = wm.month_index(2026, 1)


def series(values: list[int], start: int = BASE, gaps: tuple[int, ...] = ()) -> list[tuple[int, int]]:
    return [(start + i, v) for i, v in enumerate(values) if i not in gaps]


def test_month_index_roundtrip() -> None:
    assert wm.index_to_month(wm.month_index(2026, 12)) == "2026-12"
    assert wm.index_to_month(wm.month_index(2027, 1)) == "2027-01"
    assert wm.month_index(2027, 1) - wm.month_index(2026, 12) == 1


def test_fit_trend_exact_line_and_flat() -> None:
    trend = wm.fit_trend([(0, 10.0), (1, 12.0), (2, 14.0)])
    assert (round(trend.slope, 6), round(trend.intercept, 6)) == (2.0, 10.0)
    flat = wm.fit_trend([(5, 7.0)])
    assert (flat.slope, flat.intercept) == (0.0, 7.0)


@pytest.mark.parametrize("value, expected", [(10.5, 11), (10.49, 10), (-3.2, 0), (0.5, 1), (2.5, 3)])
def test_round_people_half_up_and_floor(value, expected) -> None:
    assert wm.round_people(value) == expected


def test_forecast_requires_minimum_history() -> None:
    result = wm.forecast(series([10, 11, 12, 13, 14]), BASE + 4, horizon=3, min_months=6)
    assert (result.status, result.projections, result.monthly_trend) == ("insufficient_history", [], None)
    assert result.observed_months == 5 and result.required_months == 6
    assert wm.forecast([], None, 3, 6).status == "insufficient_history"


def test_forecast_linear_projection() -> None:
    result = wm.forecast(series([100, 102, 104, 106, 108, 110]), BASE + 5, horizon=3, min_months=6)
    assert result.status == "ok" and result.monthly_trend == 2.0
    assert result.projections == [
        {"month": "2026-07", "projected_active_headcount": 112},
        {"month": "2026-08", "projected_active_headcount": 114},
        {"month": "2026-09", "projected_active_headcount": 116},
    ]
    assert result.backtest_mae is None and "needs at least 9" in result.backtest_note


def test_forecast_never_negative() -> None:
    result = wm.forecast(series([10, 8, 6, 4, 2, 1]), BASE + 5, horizon=6, min_months=6)
    assert all(p["projected_active_headcount"] >= 0 for p in result.projections)
    assert result.projections[-1]["projected_active_headcount"] == 0


def test_missing_months_are_skipped_not_filled() -> None:
    points = series([10, 12, 0, 16, 18, 20, 22], gaps=(2,))  # month 3 missing (not zero)
    assert (BASE + 2) not in [t for t, _ in points] and len(points) == 6
    result = wm.forecast(points, points[-1][0], horizon=1, min_months=6)
    assert result.status == "ok" and result.monthly_trend == 2.0


def test_zero_headcount_is_an_observation() -> None:
    result = wm.forecast(series([0, 0, 0, 0, 0, 0]), BASE + 5, horizon=2, min_months=6)
    assert [p["projected_active_headcount"] for p in result.projections] == [0, 0]


def test_backtest_on_held_out_observations() -> None:
    values = [50, 52, 54, 56, 58, 60, 70, 70, 70]  # last 3 depart from the trend
    result = wm.forecast(series(values), BASE + 8, horizon=1, min_months=6)
    # Trained on 50..60 (slope 2), predicts 62, 64, 66 vs observed 70, 70, 70 -> MAE 6.0
    assert result.backtest_mae == 6.0
    assert "past error does not guarantee" in result.backtest_note


def test_turnover_rate_denominators() -> None:
    assert wm.turnover_rate(3, [100, 50]) == 0.04
    assert wm.turnover_rate(0, [10, 10]) == 0.0
    assert wm.turnover_rate(2, []) is None
    assert wm.turnover_rate(2, [0, 0]) is None


def stability(values: list[int], departures: int | None, age: int | None = 5, step_days: int = 30) -> dict:
    start = date(2026, 1, 31)
    obs = [(start + timedelta(days=i * step_days), v) for i, v in enumerate(values)]
    return wm.stability(wm.StabilityInputs(obs, departures, age, 45))


def test_stability_requires_three_observations() -> None:
    result = stability([100, 100], 0)
    assert (result["status"], result["score"]) == ("insufficient_data", None)


def test_stability_stale_data() -> None:
    result = stability([100, 100, 100], 0, age=60)
    assert (result["status"], result["score"]) == ("stale_data", None)


def test_stability_missing_departures_is_not_zero() -> None:
    result = stability([100, 100, 100], None)
    assert result["status"] == "insufficient_data" and result["missing_metrics"] == ["annualized_turnover"]
    assert result["metrics"]["annualized_turnover"] is None


def test_stability_perfectly_stable_with_zero_departures() -> None:
    result = stability([100, 100, 100], 0)
    assert result["status"] == "ok"
    assert result["components"] == {"turnover": 100.0, "net_change": 100.0, "volatility": 100.0}
    assert result["score"] == 100.0


def test_stability_formula() -> None:
    # 60-day span, 100 -> 110 -> 90, 6 departures.
    result = stability([100, 110, 90], 6)
    metrics = result["metrics"]
    assert metrics["net_change_rate"] == -0.1
    assert metrics["annualized_turnover"] == round(6 / 100 * 365 / 60, 4)
    assert metrics["coefficient_of_variation"] == round(((200 / 3) ** 0.5) / 100, 4)
    expected = round(0.5 * 0.0 + 0.25 * 50.0 + 0.25 * round(100 * max(0, 1 - metrics["coefficient_of_variation"] / 0.1), 1), 1)
    assert result["components"]["turnover"] == 0.0  # 36.5% annualized exceeds the 30% zero point
    assert result["components"]["net_change"] == 50.0
    assert result["score"] == expected


def test_stability_reproducible() -> None:
    assert stability([100, 104, 98, 101], 2) == stability([100, 104, 98, 101], 2)


def test_stability_zero_baseline_headcount() -> None:
    result = stability([0, 0, 0], 0)
    assert result["status"] == "insufficient_data"
    assert set(result["missing_metrics"]) == {"net_change_rate", "coefficient_of_variation", "annualized_turnover"}


# --- Sub-prompt 27: population size, reason codes, forecast freshness and gaps --------------------------------------


def sized(values: list[int], departures: int | None, minimum: int, age: int | None = 5) -> dict:
    start = date(2026, 1, 31)
    obs = [(start + timedelta(days=i * 30), v) for i, v in enumerate(values)]
    return wm.stability(wm.StabilityInputs(obs, departures, age, 45, min_average_headcount=minimum))


def test_small_population_is_insufficient_not_scored() -> None:
    # 4 -> 3 -> 2 people: unscored, rather than a "very unstable" 0-ish score driven by one or two people.
    result = sized([4, 3, 2], 0, minimum=10)
    assert (result["status"], result["reason_code"], result["score"], result["components"]) == (
        "insufficient_data", "small_population", None, None)
    assert result["metrics"]["average_active_headcount"] == 3.0
    assert result["metrics"]["net_change_rate"] == -0.5  # observed metrics are still reported
    assert "below 10" in result["reason"]


def test_population_threshold_boundary_and_default() -> None:
    assert sized([10, 10, 10], 0, minimum=10)["status"] == "ok"  # exactly at the threshold
    assert sized([9, 10, 10], 0, minimum=10)["reason_code"] == "small_population"  # average 9.67
    assert stability([4, 4, 4], 0)["status"] == "ok"  # callers that do not opt in keep the original behaviour


def test_reason_codes_for_every_unscored_status() -> None:
    assert stability([100, 100], 0)["reason_code"] == "too_few_snapshots"
    assert stability([100, 100, 100], 0, age=60)["reason_code"] == "stale_snapshots"
    assert stability([100, 100, 100], None)["reason_code"] == "missing_metrics"
    assert stability([100, 100, 100], 0)["reason_code"] is None


def test_stale_check_precedes_population_check() -> None:
    assert sized([4, 4, 4], 0, minimum=10, age=60)["status"] == "stale_data"


@pytest.mark.parametrize("values, departures, expected_net, expected_score_below", [
    ([100, 110, 120], 0, 0.2, 75.0),  # growth of 20% zeroes the net-change component
    ([100, 95, 90], 10, -0.1, 50.0),  # shrinking with recorded departures
])
def test_headcount_changes_lower_the_score(values, departures, expected_net, expected_score_below) -> None:
    result = stability(values, departures)
    assert result["status"] == "ok" and result["metrics"]["net_change_rate"] == expected_net
    assert result["score"] < expected_score_below


def test_forecast_stable_and_declining_trends() -> None:
    flat = wm.forecast(series([40] * 6), BASE + 5, horizon=3, min_months=6)
    assert flat.monthly_trend == 0.0 and [p["projected_active_headcount"] for p in flat.projections] == [40, 40, 40]
    down = wm.forecast(series([60, 57, 54, 51, 48, 45]), BASE + 5, horizon=2, min_months=6)
    assert down.monthly_trend == -3.0 and [p["projected_active_headcount"] for p in down.projections] == [42, 39]


def test_forecast_refuses_stale_history() -> None:
    points = series([100, 102, 104, 106, 108, 110])
    stale = wm.forecast(points, BASE + 5, horizon=3, min_months=6, latest_age_days=46, max_age_days=45)
    assert (stale.status, stale.projections, stale.monthly_trend, stale.backtest_mae) == ("stale_data", [], None, None)
    assert "older than 45 days" in stale.reason
    fresh = wm.forecast(points, BASE + 5, horizon=3, min_months=6, latest_age_days=45, max_age_days=45)
    assert fresh.status == "ok" and fresh.reason is None
    unknown_age = wm.forecast(points, BASE + 5, horizon=3, min_months=6, latest_age_days=None, max_age_days=45)
    assert unknown_age.status == "stale_data"
    # Too little history is reported first, whatever the age.
    assert wm.forecast(points[:3], BASE + 2, 3, 6, latest_age_days=400, max_age_days=45).status == "insufficient_history"


def test_forecast_counts_missing_months_and_rejects_duplicates() -> None:
    points = series([10, 12, 0, 16, 0, 20, 22, 24], gaps=(2, 4))
    result = wm.forecast(points, points[-1][0], horizon=1, min_months=6)
    assert result.missing_months == 2 and result.observed_months == 6
    assert wm.missing_months([]) == 0
    with pytest.raises(ValueError):
        wm.forecast([(BASE, 10), (BASE, 11)], BASE, horizon=1, min_months=1)


def test_forecast_is_order_independent_and_deterministic() -> None:
    points = series([100, 103, 101, 106, 108, 107])
    shuffled = [points[i] for i in (3, 0, 5, 1, 4, 2)]
    assert wm.forecast(points, BASE + 5, 4, 6) == wm.forecast(shuffled, BASE + 5, 4, 6)
