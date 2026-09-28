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

Alongside it goes the **hit rate**: how often the window this planner would
have recommended — same duration, same search window, at this horizon — has
historically landed in the cleanest third of that run's feasible windows. That
is measured by replaying the planner over the scored register, not modelled. It may well be unimpressive at 48 hours. Publishing it anyway is the
point of the project.

Nothing here writes to the database. Nothing here trains. The planner is a
pure function of the champion's most recent forecast and the live accuracy
mart, both of which are precomputed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from api.routers.forecast import latest_champion_run
from gridcast.db import fetch_all

router = APIRouter(prefix="/v1", tags=["plan"])

# Horizon group boundaries, matching PREREGISTRATION §3 and mart_live_accuracy.
HORIZON_GROUPS = [(1, 6, "H1"), (7, 24, "H2"), (25, 48, "H3"), (49, 96, "H4")]

PERIOD = timedelta(minutes=30)

# The pipeline issues every 30 minutes. A forecast older than this means runs
# have been missed, and the response says so rather than letting a plan built
# on it look as fresh as any other.
STALE_AFTER = timedelta(hours=2)


def _now() -> datetime:
    """The current time. A function so tests can hold it still."""
    return datetime.now(UTC)


def _current_period_start(now: datetime) -> datetime:
    """Start of the settlement period `now` falls in."""
    return now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)


def _horizon_group(horizon: int) -> str:
    for lo, hi, name in HORIZON_GROUPS:
        if lo <= horizon <= hi:
            return name
    return "H4"


def _load_champion_forecast() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Load the champion's latest forecast. Returns (meta, horizons)."""
    latest = latest_champion_run()
    if not latest:
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


def _window_means(periods: list[dict[str, Any]], window_size: int) -> list[tuple[int, float]]:
    """Every feasible window as (start index, mean intensity).

    A window is feasible only if its periods are consecutive in time. Counting
    positions is not enough: a run missing some horizons would otherwise let a
    "two-hour" window quietly span three, and the recommendation would describe
    a load that cannot actually run in the time it claims.
    """
    span = PERIOD * (window_size - 1)
    windows = []
    for index in range(len(periods) - window_size + 1):
        block = periods[index : index + window_size]
        if block[-1]["target_sp_start_utc"] - block[0]["target_sp_start_utc"] != span:
            continue
        mean = sum(float(p["point_gco2_kwh"]) for p in block) / window_size
        windows.append((index, mean))
    return windows


def _window_starting_at_hour(
    periods: list[dict[str, Any]],
    windows: list[tuple[int, float]],
    window_size: int,
    hour_local: int,
) -> dict[str, Any] | None:
    """The feasible window beginning at a given local hour, if there is one.

    Europe/London, not UTC: the folk heuristic is "three in the morning" as a
    human experiences it, and for half the year those differ by an hour.
    """
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    for index, mean in windows:
        local = periods[index]["target_sp_start_utc"].astimezone(london)
        if local.hour == hour_local and local.minute == 0:
            block = periods[index : index + window_size]
            return {
                "index": index,
                "mean": mean,
                "start_utc": block[0]["target_sp_start_utc"],
                "end_utc": block[-1]["target_sp_start_utc"],
            }
    return None


def _search_periods(
    horizons: list[dict[str, Any]], now: datetime, within_hours: float
) -> list[dict[str, Any]]:
    """The periods a plan made at `now` may choose from, in time order.

    Measured from the clock, not from the forecast: periods that have ended are
    dropped, the one in progress is kept because it is "now", and nothing past
    `within_hours` is considered. Shared by `plan()` and the hit-rate replay so
    that the decision being scored is the decision being served.
    """
    search_start = _current_period_start(now)
    search_end = search_start + timedelta(hours=within_hours)
    return sorted(
        (p for p in horizons if search_start <= p["target_sp_start_utc"] < search_end),
        key=lambda p: p["target_sp_start_utc"],
    )


def _hit_rate(
    model_version: str,
    horizon_group: str,
    duration_hours: float,
    within_hours: float,
) -> dict[str, Any]:
    """How often the planner's pick actually landed in the cleanest third.

    Measured by replaying the planner over the scored register, not modelled.
    For every past run, rebuild the request as it would have been answered at
    issue time — the same search window, the same `duration_hours`, the same
    feasibility rule — take the window the planner would have recommended, and
    ask where it fell among that run's feasible windows once the actuals
    arrived. If its actual mean landed in the lowest third, the recommendation
    did its job.

    A run counts only once every period in its search window has been scored.
    Scoring a partly matured run against the windows that happen to have
    actuals would compare the pick against a different menu from the one it
    was chosen from.

    Each decision is filed under the horizon group of the window it picked —
    the same rule `plan()` uses to label the recommendation — so the rate shown
    beside a recommendation is the record of recommendations like it.

    A forecast can have respectable MAE and still choose badly: what matters to
    someone shifting a load is not how close the number was, but whether the
    window it pointed at turned out to be a good one. Those are different
    questions and only the second one is the product.

    This may be unimpressive at 48 hours. It is published either way.
    """
    window_size = max(1, int(duration_hours * 2))
    # Horizon 1 starts one period after the run's own period, so a search of
    # `within_hours` never reaches past this horizon.
    max_horizon = int(within_hours * 2) + 1
    rows = fetch_all(
        """
        SELECT f.run_at_utc,
               f.horizon_periods,
               f.target_sp_start_utc,
               f.point_gco2_kwh,
               s.actual_gco2_kwh
          FROM register.reg_forecast_point f
          LEFT JOIN register.reg_forecast_score s ON s.forecast_id = f.forecast_id
         WHERE f.model_version = %(model)s
           AND f.horizon_periods <= %(max_horizon)s
         ORDER BY f.run_at_utc, f.horizon_periods
        """,
        {"model": model_version, "max_horizon": max_horizon},
        readonly=True,
    )

    runs: dict[datetime, list[dict[str, Any]]] = {}
    for row in rows:
        runs.setdefault(row["run_at_utc"], []).append(row)

    decisions = 0
    hits = 0
    for run_at_utc, horizons in runs.items():
        periods = _search_periods(horizons, run_at_utc, within_hours)
        if not periods or any(p["actual_gco2_kwh"] is None for p in periods):
            continue
        windows = _window_means(periods, window_size)
        if len(windows) < 3:
            continue

        # The planner's pick: lowest forecast mean, earliest on a tie.
        best_start, _ = min(windows, key=lambda w: w[1])
        if _horizon_group(int(periods[best_start]["horizon_periods"])) != horizon_group:
            continue

        actual_by_start = {
            index: sum(float(p["actual_gco2_kwh"]) for p in periods[index : index + window_size])
            / window_size
            for index, _ in windows
        }
        actuals = list(actual_by_start.values())
        chosen = actual_by_start[best_start]
        # percent_rank: the share of other windows that were strictly cleaner.
        cleaner = sum(1 for a in actuals if a < chosen)
        decisions += 1
        if cleaner / (len(actuals) - 1) <= 1 / 3:
            hits += 1

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
            f"Of {decisions:,} past {duration_hours:g}h recommendations within "
            f"{within_hours:g}h at this horizon, {hits:,} landed in the cleanest "
            f"third of their feasible windows. Picking at random would land there "
            f"about a third of the time."
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

    # The search window is measured from the clock, not from the forecast.
    #
    # Slicing the first N horizons assumed the latest run was issued moments
    # ago. When the pipeline stalls it was not, and the leading horizons are
    # periods that have already happened — a "best window" among them is a
    # recommendation to run the dishwasher yesterday. Periods that have ended
    # are dropped; the one in progress is kept, because it is "now".
    now = _now()
    age = now - run_at_utc
    stale = age > STALE_AFTER
    freshness = {
        "forecast_age_minutes": int(age.total_seconds() // 60),
        "stale": stale,
    }
    search_start = _current_period_start(now)
    window_size = max(1, int(duration_hours * 2))
    periods = _search_periods(horizons, now, within_hours)
    windows = _window_means(periods, window_size)

    if not windows:
        reason = (
            f"the latest forecast was issued at {run_at_utc.isoformat()} and does not "
            f"cover the next {within_hours}h — the pipeline may have stalled"
            if stale
            else f"no unbroken {duration_hours}h run of forecast periods in the "
            f"next {within_hours}h ({len(periods)} periods available)"
        )
        return {
            "model_version": model_version,
            "run_at_utc": run_at_utc,
            **freshness,
            "detail": f"not enough forecast periods: {reason}",
        }

    # Find best and worst windows. min/max keep the earliest on a tie.
    best_start, best_mean = min(windows, key=lambda w: w[1])
    worst_start, worst_mean = max(windows, key=lambda w: w[1])

    best_periods = periods[best_start : best_start + window_size]
    worst_periods = periods[worst_start : worst_start + window_size]

    # "Now" counterfactual: the soonest window that can start. Issuing never
    # forecasts the period already under way, so that is normally the next one.
    # A gap at the front means there is no honest figure for running
    # immediately, and none is invented.
    now_window = (
        windows[0]
        if windows[0][0] == 0 and periods[0]["target_sp_start_utc"] <= search_start + PERIOD
        else None
    )

    # "Average" counterfactual: the mean over every feasible window start.
    #
    # This is the expected value of choosing a feasible start uniformly at
    # random, which is the honest baseline: it is what a user gets by not
    # thinking about it. It is the mean of window means, not of periods — the
    # two differ whenever gaps break some windows up, or the edges of the
    # search are counted in fewer windows than its middle.
    all_mean = sum(mean for _, mean in windows) / len(windows)

    # "Overnight" counterfactual: 03:00 local, the folk heuristic.
    #
    # Worth measuring precisely because it is what people already believe. On a
    # wind-driven grid the cleanest hours often are not overnight at all, so
    # this comparison can come out negative — the recommendation being worse
    # than the habit. That result would be reported, not suppressed.
    overnight = _window_starting_at_hour(periods, windows, window_size, hour_local=3)

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
        "hit_rate": _hit_rate(model_version, group, duration_hours, within_hours),
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
            # `is not None`, not truthiness: 0.0 is a forecast, not a missing one.
            "q10_gco2_kwh": (
                float(p["q10_gco2_kwh"]) if p.get("q10_gco2_kwh") is not None else None
            ),
            "q90_gco2_kwh": (
                float(p["q90_gco2_kwh"]) if p.get("q90_gco2_kwh") is not None else None
            ),
        }

    return {
        "model_version": model_version,
        "run_at_utc": run_at_utc,
        **freshness,
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
            "now": (
                {
                    "mean_gco2_kwh": round(now_window[1], 1),
                    "note": (
                        "Running immediately. Flatters the tool whenever now happens to be dirty."
                    ),
                    **_saving(
                        now_window[1],
                        best_mean,
                        appliance_kwh,
                        baseline_block=periods[:window_size],
                    ),
                }
                if now_window
                else {"note": "The forecast has no unbroken window starting now."}
            ),
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
