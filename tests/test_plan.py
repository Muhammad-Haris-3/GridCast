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
) -> None:
    """Stand a fake register behind both routers."""

    def fake_fetch_all(query: str, params: Any = None, **_: Any) -> list[dict[str, Any]]:
        if "role = 'champion'" in query:
            return [dict(row) for row in champions]
        if "FROM register.reg_forecast_point" in query:
            return [dict(p) for p in points[params[0]]]
        return []  # accuracy mart: nothing scored yet

    def fake_fetch_one(query: str, params: Any = None, **_: Any) -> dict[str, Any] | None:
        if "role = 'champion'" in query:
            # What the old query did: whichever champion row came back first.
            return dict(champions[0]) if champions else None
        return {"decisions": 0, "hits": 0}  # hit rate: nothing scored yet

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
