"""Appliance planner — the decision the forecast exists to support (M8).

    GET /v1/plan?duration_hours=2&within_hours=12&appliance_kwh=1.2

Given a flexible load that needs to run for `duration_hours` sometime in the
next `within_hours`, the planner names the half-hour window with the lowest
forecast carbon intensity and reports what the choice saves against three
counterfactuals:

  * **now** — running immediately
  * **worst** — the highest-intensity window in the search range
  * **average** — the mean across the whole search range

It also reports the champion model's historical accuracy at the recommended
window's horizon, so the user can judge how much to trust the recommendation.

Nothing here writes to the database. Nothing here trains. The planner is a
pure function of the champion's most recent forecast and the live accuracy
mart, both of which are precomputed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from gridcast.db import fetch_all, fetch_one

router = APIRouter(prefix="/v1", tags=["plan"])

# Horizon group boundaries, matching PREREGISTRATION §3 and mart_live_accuracy.
HORIZON_GROUPS = [(1, 6, "H1"), (7, 24, "H2"), (25, 48, "H3"), (49, 96, "H4")]


def _horizon_group(horizon: int) -> str:
    for lo, hi, name in HORIZON_GROUPS:
        if lo <= horizon <= hi:
            return name
    return "H4"


def _load_champion_forecast() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Load the champion's latest forecast. Returns (meta, horizons)."""
    latest = fetch_one(
        """
        SELECT m.model_version, max(f.run_at_utc) AS run_at_utc
          FROM register.reg_forecast_point f
          JOIN register.reg_model_version m ON m.model_version = f.model_version
         WHERE m.role = 'champion'
         GROUP BY m.model_version
        """,
        readonly=True,
    )
    if not latest or latest["run_at_utc"] is None:
        return None, []

    horizons = fetch_all(
        """
        SELECT horizon_periods, target_sp_start_utc,
               point_gco2_kwh, q10_gco2_kwh, q90_gco2_kwh,
               q025_gco2_kwh, q975_gco2_kwh
          FROM register.reg_forecast_point
         WHERE model_version = %s AND run_at_utc = %s
         ORDER BY horizon_periods
        """,
        (latest["model_version"], latest["run_at_utc"]),
        readonly=True,
    )
    return latest, horizons


def _load_accuracy(model_version: str) -> dict[str, dict[str, Any]]:
    """Load live accuracy by horizon group for a model. Returns {group: row}."""
    rows = fetch_all(
        """
        SELECT horizon_group, n, mae, rmse, mase
          FROM marts.mart_live_accuracy
         WHERE model_version = %s
        """,
        (model_version,),
        readonly=True,
    )
    return {row["horizon_group"]: row for row in rows}


def _sliding_window_min(periods: list[dict[str, Any]], window_size: int) -> tuple[int, float]:
    """Find the starting index of the lowest-mean window. Returns (index, mean)."""
    if window_size > len(periods):
        window_size = len(periods)

    # Compute the first window sum.
    current_sum = sum(float(periods[i]["point_gco2_kwh"]) for i in range(window_size))
    best_sum = current_sum
    best_start = 0

    # Slide.
    for i in range(1, len(periods) - window_size + 1):
        current_sum -= float(periods[i - 1]["point_gco2_kwh"])
        current_sum += float(periods[i + window_size - 1]["point_gco2_kwh"])
        if current_sum < best_sum:
            best_sum = current_sum
            best_start = i

    return best_start, best_sum / window_size


def _sliding_window_max(periods: list[dict[str, Any]], window_size: int) -> tuple[int, float]:
    """Find the starting index of the highest-mean window."""
    if window_size > len(periods):
        window_size = len(periods)

    current_sum = sum(float(periods[i]["point_gco2_kwh"]) for i in range(window_size))
    worst_sum = current_sum
    worst_start = 0

    for i in range(1, len(periods) - window_size + 1):
        current_sum -= float(periods[i - 1]["point_gco2_kwh"])
        current_sum += float(periods[i + window_size - 1]["point_gco2_kwh"])
        if current_sum > worst_sum:
            worst_sum = current_sum
            worst_start = i

    return worst_start, worst_sum / window_size


@router.get("/plan")
def plan(
    duration_hours: float = Query(
        default=1.0,
        ge=0.5,
        le=8.0,
        description="How long the appliance runs, in hours.",
    ),
    within_hours: float = Query(
        default=24.0,
        ge=1.0,
        le=48.0,
        description="Search for the best window within this many hours.",
    ),
    appliance_kwh: float = Query(
        default=1.0,
        ge=0.01,
        le=100.0,
        description="Total energy consumption in kWh, for absolute CO₂ calculation.",
    ),
) -> dict[str, Any]:
    """Find the lowest-carbon window to run a flexible load.

    The recommendation is the contiguous block of `duration_hours` half-hour
    periods within the next `within_hours` that has the lowest mean forecast
    carbon intensity. Three counterfactuals show what the choice saves.
    """
    latest, horizons = _load_champion_forecast()
    if not latest or not horizons:
        return {
            "model_version": None,
            "run_at_utc": None,
            "detail": "no forecasts available — the pipeline may not have run yet",
        }

    model_version = latest["model_version"]
    run_at_utc = latest["run_at_utc"]

    # Filter to the search window.
    search_periods = int(within_hours * 2)  # half-hourly periods
    window_size = max(1, int(duration_hours * 2))
    periods = horizons[:search_periods]

    if len(periods) < window_size:
        return {
            "model_version": model_version,
            "run_at_utc": run_at_utc,
            "detail": (
                f"not enough forecast periods: need {window_size} for "
                f"{duration_hours}h but only {len(periods)} available"
            ),
        }

    # Find best and worst windows.
    best_start, best_mean = _sliding_window_min(periods, window_size)
    worst_start, worst_mean = _sliding_window_max(periods, window_size)

    best_periods = periods[best_start : best_start + window_size]
    worst_periods = periods[worst_start : worst_start + window_size]

    # "Now" counterfactual: the first `window_size` periods.
    now_mean = sum(float(p["point_gco2_kwh"]) for p in periods[:window_size]) / window_size

    # "Average" counterfactual: mean of ALL periods in the search window.
    all_mean = sum(float(p["point_gco2_kwh"]) for p in periods) / len(periods)

    # Savings.
    def _saving(baseline: float, recommended: float, kwh: float) -> dict[str, Any]:
        saving_gco2_kwh = baseline - recommended
        saving_pct = (saving_gco2_kwh / baseline * 100) if baseline else 0
        co2_saved_g = saving_gco2_kwh * kwh
        return {
            "saving_gco2_kwh": round(saving_gco2_kwh, 1),
            "saving_pct": round(saving_pct, 1),
            "co2_saved_g": round(co2_saved_g, 1),
        }

    # Horizon info for the recommended window.
    min_horizon = int(best_periods[0]["horizon_periods"])
    max_horizon = int(best_periods[-1]["horizon_periods"])
    group = _horizon_group(min_horizon)

    # Historical accuracy at this horizon group.
    accuracy = _load_accuracy(model_version)
    group_accuracy = accuracy.get(group)

    confidence: dict[str, Any] = {"horizon_group": group}
    if group_accuracy:
        confidence["mae_gco2_kwh"] = float(group_accuracy["mae"])
        confidence["n"] = group_accuracy["n"]
        confidence["note"] = (
            f"At this horizon ({group}), the model's mean absolute error is "
            f"{group_accuracy['mae']} gCO₂/kWh over {group_accuracy['n']:,} scored points."
        )
    else:
        confidence["note"] = (
            "No live accuracy data yet for this horizon group. "
            "The model is still accumulating scored points."
        )

    # Serialize period rows for the response.
    def _period_row(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "target_sp_start_utc": p["target_sp_start_utc"],
            "horizon_periods": p["horizon_periods"],
            "point_gco2_kwh": float(p["point_gco2_kwh"]),
            "q10_gco2_kwh": float(p["q10_gco2_kwh"]) if p.get("q10_gco2_kwh") else None,
            "q90_gco2_kwh": float(p["q90_gco2_kwh"]) if p.get("q90_gco2_kwh") else None,
        }

    return {
        "model_version": model_version,
        "run_at_utc": run_at_utc,
        "search_window_hours": within_hours,
        "duration_hours": duration_hours,
        "appliance_kwh": appliance_kwh,
        "best_window": {
            "start_utc": best_periods[0]["target_sp_start_utc"],
            "end_utc": best_periods[-1]["target_sp_start_utc"],
            "mean_gco2_kwh": round(best_mean, 1),
            "periods": [_period_row(p) for p in best_periods],
            "horizon_group": group,
            "min_horizon": min_horizon,
            "max_horizon": max_horizon,
        },
        "counterfactuals": {
            "now": {
                "mean_gco2_kwh": round(now_mean, 1),
                **_saving(now_mean, best_mean, appliance_kwh),
            },
            "worst": {
                "mean_gco2_kwh": round(worst_mean, 1),
                "start_utc": worst_periods[0]["target_sp_start_utc"],
                "end_utc": worst_periods[-1]["target_sp_start_utc"],
                **_saving(worst_mean, best_mean, appliance_kwh),
            },
            "average": {
                "mean_gco2_kwh": round(all_mean, 1),
                **_saving(all_mean, best_mean, appliance_kwh),
            },
        },
        "all_periods": [_period_row(p) for p in periods],
        "confidence": confidence,
    }
