"""The planner's choices, tested against a register it cannot see past.

No database. Each test hands the endpoint a fake register — a champion row or
two and a run of forecast points — and holds the clock still, so the thing
under test is the planner's own reasoning: which periods it will consider,
which model it serves, and what it reports.

Every test here failed against the planner as it was before these fixes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import forecast as forecast_router
from api.routers import plan as plan_router

NOW = datetime(2026, 6, 1, 12, 10, tzinfo=UTC)
PERIOD = timedelta(minutes=30)


def _points(run_at: datetime, values: list[float], *, skip: set[int] = frozenset()) -> list:
    """Forecast points at horizons 1..len(values), minus any in `skip`."""
    first = run_at.replace(minute=0 if run_at.minute < 30 else 30, second=0, microsecond=0)
    return [
        {
            "horizon_periods": h,
            "target_sp_start_utc": first + PERIOD * h,
            "point_gco2_kwh": value,
            "q10_gco2_kwh": value - 20,
            "q90_gco2_kwh": value + 20,
            "q025_gco2_kwh": None,
            "q975_gco2_kwh": None,
        }
        for h, value in enumerate(values, start=1)
        if h not in skip
    ]


def _install(
    monkeypatch: pytest.MonkeyPatch,
    champions: list[dict[str, Any]],
    points: dict[str, list[dict[str, Any]]],
    scored: list[dict[str, Any]] = (),
) -> None:
    """Stand a fake register behind both routers.

    `scored` is what the hit-rate replay reads: forecast points joined to their
    actuals, with `actual_gco2_kwh` None where no score has arrived yet.
    """

    def fake_fetch_all(query: str, params: Any = None, **_: Any) -> list[dict[str, Any]]:
        if "role = 'champion'" in query:
            return [dict(row) for row in champions]
        if "reg_forecast_score" in query:
            return [dict(row) for row in scored if row["horizon_periods"] <= params["max_horizon"]]
        if "FROM register.reg_forecast_point" in query:
            return [dict(p) for p in points[params[0]]]
        return []  # accuracy mart: nothing scored yet

    def fake_fetch_one(query: str, params: Any = None, **_: Any) -> dict[str, Any] | None:
        if "role = 'champion'" in query:
            # What the old query did: whichever champion row came back first.
            return dict(champions[0]) if champions else None
        return None

    for module in (forecast_router, plan_router):
        monkeypatch.setattr(module, "fetch_all", fake_fetch_all, raising=False)
        monkeypatch.setattr(module, "fetch_one", fake_fetch_one, raising=False)
    monkeypatch.setattr(plan_router, "_now", lambda: NOW, raising=False)


def _champion(version: str, run_at: datetime, promoted: datetime) -> dict[str, Any]:
    return {"model_version": version, "run_at_utc": run_at, "role_since_utc": promoted}


def _plan(**params: Any) -> dict[str, Any]:
    response = TestClient(app).get("/v1/plan", params=params)
    assert response.status_code == 200
    return response.json()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_a_stale_forecast_never_recommends_a_window_in_the_past(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Issued six hours ago, and cleanest over its first few horizons — which
    # have all already happened.
    run_at = NOW - timedelta(hours=6)
    values = [50.0] * 8 + [200.0] * 88
    _install(monkeypatch, [_champion("m1", run_at, run_at)], {"m1": _points(run_at, values)})

    body = _plan(duration_hours=1, within_hours=12)

    current_period = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert _ts(body["best_window"]["start_utc"]) >= current_period
    assert all(_ts(p["target_sp_start_utc"]) >= current_period for p in body["all_periods"])
    assert body["stale"] is True
    assert body["forecast_age_minutes"] == 360


def test_a_forecast_that_has_run_out_says_so_instead_of_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_at = NOW - timedelta(hours=3)
    _install(monkeypatch, [_champion("m1", run_at, run_at)], {"m1": _points(run_at, [100.0] * 4)})

    body = _plan(duration_hours=1, within_hours=12)

    assert "best_window" not in body
    assert body["stale"] is True
    assert "stalled" in body["detail"]


def test_two_champions_resolve_the_same_way_whatever_order_they_arrive_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_at = NOW - timedelta(minutes=10)
    older = _champion("m-old", run_at, datetime(2026, 1, 1, tzinfo=UTC))
    newer = _champion("m-new", run_at, datetime(2026, 5, 1, tzinfo=UTC))
    points = {"m-old": _points(run_at, [100.0] * 96), "m-new": _points(run_at, [150.0] * 96)}

    served = set()
    for order in ([older, newer], [newer, older]):
        _install(monkeypatch, order, points)
        served.add(_plan(duration_hours=1, within_hours=12)["model_version"])
        served.add(TestClient(app).get("/v1/forecast/current").json()["model_version"])

    assert served == {"m-new"}


def test_a_window_is_measured_in_time_not_in_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    # Horizons 3-6 are missing. Positionally, horizons 2 and 7 sit next to each
    # other and form the cleanest "one-hour" window — but they are three hours
    # apart, so that load could not run in the hour the plan claims.
    run_at = NOW - timedelta(minutes=10)
    # Every real one-hour window touching them is dirtier than the plain 100s.
    values = [300.0, 10.0, 0, 0, 0, 0, 10.0, 300.0] + [100.0] * 88
    _install(
        monkeypatch,
        [_champion("m1", run_at, run_at)],
        {"m1": _points(run_at, values, skip={3, 4, 5, 6})},
    )

    body = _plan(duration_hours=1, within_hours=12)

    window = body["best_window"]
    span = _ts(window["end_utc"]) - _ts(window["start_utc"])
    assert span == PERIOD
    assert window["mean_gco2_kwh"] == 100.0


def test_within_hours_bounds_the_search_by_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # A gap early on would push a positional slice further into the future than
    # asked for. The cleanest period sits just past the 2-hour limit.
    run_at = NOW - timedelta(minutes=10)
    values = [100.0] * 5 + [10.0] + [100.0] * 90
    _install(
        monkeypatch,
        [_champion("m1", run_at, run_at)],
        {"m1": _points(run_at, values, skip={2, 3})},
    )

    body = _plan(duration_hours=0.5, within_hours=2)

    limit = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    assert all(_ts(p["target_sp_start_utc"]) < limit for p in body["all_periods"])
    assert body["best_window"]["mean_gco2_kwh"] == 100.0


def test_a_zero_quantile_is_a_value_not_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    run_at = NOW - timedelta(minutes=10)
    points = _points(run_at, [100.0] * 96)
    for p in points:
        p["q10_gco2_kwh"] = 0.0
    _install(monkeypatch, [_champion("m1", run_at, run_at)], {"m1": points})

    body = _plan(duration_hours=1, within_hours=12)

    assert all(p["q10_gco2_kwh"] == 0.0 for p in body["all_periods"])
    assert all(p["q10_gco2_kwh"] == 0.0 for p in body["best_window"]["periods"])


def test_now_is_the_soonest_period_that_can_start(monkeypatch: pytest.MonkeyPatch) -> None:
    run_at = NOW - timedelta(minutes=10)
    _install(
        monkeypatch,
        [_champion("m1", run_at, run_at)],
        {"m1": _points(run_at, [300.0, 300.0] + [100.0] * 94)},
    )

    body = _plan(duration_hours=1, within_hours=12)

    assert body["stale"] is False
    assert body["counterfactuals"]["now"]["mean_gco2_kwh"] == 300.0


def test_average_is_the_mean_over_feasible_window_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Three periods, two one-hour windows: (0, 0) and (0, 300). Picking a start
    # at random averages 75. The mean of the periods is 100, which weights the
    # dirty edge period as if it began a window of its own.
    run_at = NOW - timedelta(minutes=10)
    _install(
        monkeypatch,
        [_champion("m1", run_at, run_at)],
        {"m1": _points(run_at, [0.0, 0.0, 300.0] + [100.0] * 93)},
    )

    body = _plan(duration_hours=1, within_hours=2)

    assert body["counterfactuals"]["average"]["mean_gco2_kwh"] == 75.0


def _scored(run_at: datetime, forecast: list[float], actual: list[float | None]) -> list:
    """Scored register rows for one run, at horizons 1..len(forecast)."""
    return [
        {
            "run_at_utc": run_at,
            "horizon_periods": h,
            "target_sp_start_utc": run_at + PERIOD * h,
            "point_gco2_kwh": f,
            "actual_gco2_kwh": a,
        }
        for h, (f, a) in enumerate(zip(forecast, actual, strict=True), start=1)
    ]


def test_hit_rate_scores_the_window_the_planner_would_have_picked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One-hour loads within 3.5 hours: horizons 1-6, five feasible windows.
    # The cleanest forecast *period* (h2) really was clean, so a per-period
    # replay calls this a hit. But the planner recommends a *window*, h1-h2,
    # whose actual mean of 150 was beaten by two of the other four windows.
    # That is not the cleanest third.
    run_at = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    rows = _scored(
        run_at,
        [100.0, 10.0, 100.0, 100.0, 100.0, 100.0],
        [300.0, 0.0, 300.0, 0.0, 0.0, 0.0],
    )
    _install(monkeypatch, [], {}, scored=rows)

    result = plan_router._hit_rate("m1", "H1", 1.0, 3.5)

    assert result["available"] is True
    assert (result["decisions"], result["hits"]) == (1, 0)


def test_hit_rate_files_each_decision_under_its_windows_horizon_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Within five hours the planner picks h7-h8, an H2 window. It belongs in
    # the H2 record and nowhere in H1, however many H1 periods the run has.
    run_at = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    forecast = [100.0] * 6 + [10.0, 10.0] + [100.0] * 2
    actual = [200.0] * 6 + [50.0, 50.0] + [200.0] * 2
    _install(monkeypatch, [], {}, scored=_scored(run_at, forecast, actual))

    assert plan_router._hit_rate("m1", "H1", 1.0, 5.0)["available"] is False
    h2 = plan_router._hit_rate("m1", "H2", 1.0, 5.0)
    assert (h2["decisions"], h2["hits"]) == (1, 1)


def test_hit_rate_waits_until_the_whole_search_window_is_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The last period has no actual yet. Judging the pick against the windows
    # that happen to be scored would change the menu it was chosen from.
    run_at = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    rows = _scored(
        run_at,
        [10.0, 10.0, 100.0, 100.0, 100.0, 100.0],
        [0.0, 0.0, 100.0, 100.0, 100.0, None],
    )
    _install(monkeypatch, [], {}, scored=rows)

    assert plan_router._hit_rate("m1", "H1", 1.0, 3.5)["available"] is False
