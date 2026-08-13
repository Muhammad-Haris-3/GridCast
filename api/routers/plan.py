"""Appliance planner — the decision the forecast exists to support (M8).

    GET /v1/plan?duration_hours=2&within_hours=12&appliance_kwh=1.2

Given a flexible load that needs to run for `duration_hours` sometime in the
next `within_hours`, the planner names the half-hour window with the lowest
forecast carbon intensity and reports what the choice saves against three
counterfactuals:

  * **now** — running immediately
  * **average** — the expected result of picking a feasible time at random
  * **overnight** — 03:00, the folk heuristic almost everyone reaches for

`worst` is reported too, but as an explicit upper bound rather than a
counterfactual. Nobody deliberately runs their dishwasher at the dirtiest hour
of the day, so a saving measured against that choice is a number the tool cannot
honestly claim. The design says as much about "run now"; it applies more
strongly here.

`average` is the honest one. It is what you get by not thinking about it, which
is the alternative most users are actually choosing between.

The saving is reported as an interval, not a point. It is derived from the
forecast's own q10/q90 rather than asserted, because a recommendation that
quotes a single number implies a precision the forecast does not have.

Alongside it goes the **hit rate**: how often a recommendation made at this
horizon has historically landed in the cleanest third of its feasible window.
That is measured by replaying the planner over the scored register, not
modelled. It may well be unimpressive at 48 hours. Publishing it anyway is the
point of the project.

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


def _window_starting_at_hour(
    periods: list[dict[str, Any]], window_size: int, hour_local: int
) -> dict[str, Any] | None:
    """The window beginning at a given local hour, if it is inside the range.

    Europe/London, not UTC: the folk heuristic is "three in the morning" as a
    human experiences it, and for half the year those differ by an hour.
    """
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    for index, period in enumerate(periods):
        if index + window_size > len(periods):
            break
        local = period["target_sp_start_utc"].astimezone(london)
        if local.hour == hour_local and local.minute == 0:
            block = periods[index : index + window_size]
            return {
                "index": index,
                "mean": sum(float(p["point_gco2_kwh"]) for p in block) / window_size,
                "start_utc": block[0]["target_sp_start_utc"],
                "end_utc": block[-1]["target_sp_start_utc"],
            }
    return None


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


def _hit_rate(model_version: str, horizon_group: str) -> dict[str, Any]:
    """How often the planner's pick actually landed in the cleanest third.

    Measured by replaying the recommendation over the scored register, not
    modelled. For every past issue time, take the period the model ranked
    cleanest and ask where it fell once the actuals arrived. If it landed in the
    lowest tercile of what actually happened, the recommendation did its job.

    A forecast can have respectable MAE and still choose badly: what matters to
    someone shifting a load is not how close the number was, but whether the
    hour it pointed at turned out to be a good one. Those are different
    questions and only the second one is the product.

    This may be unimpressive at 48 hours. It is published either way.
    """
    row = fetch_one(
        """
        WITH scored AS (
            SELECT f.run_at_utc,
                   f.target_sp_start_utc,
                   f.point_gco2_kwh,
                   s.actual_gco2_kwh
              FROM register.reg_forecast_point f
              JOIN register.reg_forecast_score s ON s.forecast_id = f.forecast_id
             WHERE f.model_version = %(model)s
               AND CASE
                     WHEN f.horizon_periods BETWEEN 1 AND 6   THEN 'H1'
                     WHEN f.horizon_periods BETWEEN 7 AND 24  THEN 'H2'
                     WHEN f.horizon_periods BETWEEN 25 AND 48 THEN 'H3'
                     ELSE 'H4'
                   END = %(grp)s
        ),
        ranked AS (
            SELECT run_at_utc,
                   target_sp_start_utc,
                   -- What the model thought was cleanest at issue time...
                   row_number() OVER (
                       PARTITION BY run_at_utc ORDER BY point_gco2_kwh
                   ) AS forecast_rank,
                   -- ...against where it actually landed.
                   percent_rank() OVER (
                       PARTITION BY run_at_utc ORDER BY actual_gco2_kwh
                   ) AS actual_pct,
                   count(*)      OVER (PARTITION BY run_at_utc) AS candidates
              FROM scored
        )
        SELECT count(*) AS decisions,
               count(*) FILTER (WHERE actual_pct <= 0.3334) AS hits
          FROM ranked
         WHERE forecast_rank = 1
           AND candidates >= 3
        """,
        {"model": model_version, "grp": horizon_group},
        readonly=True,
    )

    decisions = row["decisions"] if row else 0
    hits = row["hits"] if row else 0

    if not decisions:
        return {
            "available": False,
            "note": (
                "No scored recommendations yet at this horizon. A forecast becomes "
                "scoreable about a day after it is issued, so this fills in as the "
                "register matures."
            ),
        }

    return {
        "available": True,
        "decisions": decisions,
        "hits": hits,
        "hit_rate": round(hits / decisions, 3),
        "baseline": 0.333,
        "note": (
            f"Of {decisions:,} recommendations at this horizon, {hits:,} landed in "
            f"the cleanest third of their feasible window. Picking at random would "
            f"land there 33.3% of the time."
        ),
    }


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
    #
    # This is the expected value of choosing a feasible start uniformly at
    # random, which is the honest baseline: it is what a user gets by not
    # thinking about it.
    all_mean = sum(float(p["point_gco2_kwh"]) for p in periods) / len(periods)

    # "Overnight" counterfactual: 03:00 local, the folk heuristic.
    #
    # Worth measuring precisely because it is what people already believe. On a
    # wind-driven grid the cleanest hours often are not overnight at all, so
    # this comparison can come out negative — the recommendation being worse
    # than the habit. That result would be reported, not suppressed.
    overnight = _window_starting_at_hour(periods, window_size, hour_local=3)

    # Savings.
    def _mean_quantile(block: list[dict[str, Any]], key: str) -> float | None:
        values = [p[key] for p in block if p.get(key) is not None]
        return sum(float(v) for v in values) / len(values) if values else None

    best_q10 = _mean_quantile(best_periods, "q10_gco2_kwh")
    best_q90 = _mean_quantile(best_periods, "q90_gco2_kwh")

    def _saving(
        baseline: float,
        recommended: float,
        kwh: float,
        *,
        baseline_block: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Saving as a central estimate with an interval around it.

        The interval comes from the forecast's own q10/q90 rather than being
        asserted. Best case is the recommended window landing at its q10 while
        the alternative lands at its q90; worst case is the reverse, and it can
        be negative — the recommendation turning out worse than the thing it was
        compared against.

        Reporting that possibility is the point. A planner that only ever quotes
        an upside is advertising, not forecasting.
        """
        saving_gco2_kwh = baseline - recommended
        saving_pct = (saving_gco2_kwh / baseline * 100) if baseline else 0

        result: dict[str, Any] = {
            "saving_gco2_kwh": round(saving_gco2_kwh, 1),
            "saving_pct": round(saving_pct, 1),
            "co2_saved_g": round(saving_gco2_kwh * kwh, 1),
        }

        base_q10 = _mean_quantile(baseline_block, "q10_gco2_kwh") if baseline_block else None
        base_q90 = _mean_quantile(baseline_block, "q90_gco2_kwh") if baseline_block else None
        if None not in (best_q10, best_q90, base_q10, base_q90):
            optimistic = base_q90 - best_q10
            pessimistic = base_q10 - best_q90
            result["saving_gco2_kwh_range"] = [round(pessimistic, 1), round(optimistic, 1)]
            result["co2_saved_g_range"] = [
                round(pessimistic * kwh, 1),
                round(optimistic * kwh, 1),
            ]
            result["could_be_worse"] = pessimistic < 0

        return result

    # Horizon info for the recommended window.
    min_horizon = int(best_periods[0]["horizon_periods"])
    max_horizon = int(best_periods[-1]["horizon_periods"])
    group = _horizon_group(min_horizon)

    # Historical accuracy at this horizon group.
    accuracy = _load_accuracy(model_version)
    group_accuracy = accuracy.get(group)

    confidence: dict[str, Any] = {
        "horizon_group": group,
        "hit_rate": _hit_rate(model_version, group),
    }
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
                "note": "Running immediately. Flatters the tool whenever now happens to be dirty.",
                **_saving(
                    now_mean,
                    best_mean,
                    appliance_kwh,
                    baseline_block=periods[:window_size],
                ),
            },
            "average": {
                "mean_gco2_kwh": round(all_mean, 1),
                "note": (
                    "The expected result of picking a feasible time at random — "
                    "what you get by not thinking about it. The honest baseline."
                ),
                **_saving(all_mean, best_mean, appliance_kwh),
            },
            "overnight": (
                {
                    "mean_gco2_kwh": round(overnight["mean"], 1),
                    "start_utc": overnight["start_utc"],
                    "end_utc": overnight["end_utc"],
                    "note": (
                        "03:00 local, the folk heuristic. On a wind-driven grid the "
                        "cleanest hours are often not overnight, so this can be negative."
                    ),
                    **_saving(
                        overnight["mean"],
                        best_mean,
                        appliance_kwh,
                        baseline_block=periods[
                            overnight["index"] : overnight["index"] + window_size
                        ],
                    ),
                }
                if overnight
                else {"note": "03:00 does not fall inside the requested search window."}
            ),
        },
        "upper_bound": {
            "mean_gco2_kwh": round(worst_mean, 1),
            "start_utc": worst_periods[0]["target_sp_start_utc"],
            "end_utc": worst_periods[-1]["target_sp_start_utc"],
            "saving_gco2_kwh": round(worst_mean - best_mean, 1),
            "note": (
                "The dirtiest feasible window. Reported as a bound, NOT a "
                "counterfactual: nobody deliberately runs a load at the worst "
                "hour, so a saving measured against it is not a saving anyone "
                "would actually make."
            ),
        },
        "all_periods": [_period_row(p) for p in periods],
        "confidence": confidence,
    }
