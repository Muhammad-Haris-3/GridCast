"""Rolling-origin backtesting (SRS FR-19, design 10.1).

    python -m gridcast.backtest --from 2019-01-01 --to 2026-08-01
    python -m gridcast.backtest --from 2025-01-01 --step-hours 24 --report

Walk-forward, never random split. Each origin issues forecasts for the next 48
hours using only what was observable at that instant, and is scored against
what actually happened.

TWO THINGS THIS HARNESS CANNOT DO, both stated here rather than buried.

1. The ESO benchmark is **not horizon-matched** in backfilled history. Measured
   2026-08-12: of 46 future periods held in the warehouse, 33 had their ESO
   forecast revised within two hours, by 5 to 8 gCO2/kWh. The ESO revises
   continuously as the horizon shortens, so the value stored against a 2019
   period is their final near-term forecast, not a 48-hour-ahead one.

   A backtest therefore compares GridCast at 48 hours against the ESO at
   something much shorter. That flatters the ESO. A GridCast win here would be a
   strong result; a GridCast loss is close to uninformative, and must never be
   reported as "the ESO forecasts better" without this caveat attached.

   Only the live scoreboard can compare like with like, because only there is
   each ESO forecast captured with the time we saw it. That is the second
   independent reason design 8.3 keeps backtest and live results in separate
   columns and never pools them — the first being reconstructed vintages.

2. Backfilled rows carry reconstructed knowability. `fetched_at_utc` on a
   backfilled row is when the backfill ran, so the harness uses period age as
   the knowability proxy instead. That is an approximation, and it is why these
   results live in a `backtest` schema of their own rather than in the register.
"""

from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from gridcast import metrics
from gridcast.baselines import BASELINES, Observed, build_climatology, climatology
from gridcast.config import get_settings
from gridcast.db import connect

HORIZONS = 96  # 48 hours of half-hour steps
PERIOD = timedelta(minutes=30)

# The gap between the end of usable training data and the issue time.
#
# Without it, an origin can train on actuals that would still have been pending
# at that moment — the most common leakage in time-series backtesting, and one
# that flatters results precisely at short horizons where a model is supposed to
# be strongest. Sized to the maturity threshold, so it moves when D-1 is
# finally measured forward rather than being an independent guess.
DEFAULT_EMBARGO_HOURS = 24

HORIZON_GROUPS = [
    ("H1", 1, 6),  # 0-3 hours
    ("H2", 7, 24),  # 3-12 hours
    ("H3", 25, 48),  # 12-24 hours
    ("H4", 49, 96),  # 24-48 hours
]


def horizon_group(horizon: int) -> str:
    for name, low, high in HORIZON_GROUPS:
        if low <= horizon <= high:
            return name
    return "H?"


@dataclass
class BacktestConfig:
    date_from: datetime
    date_to: datetime
    step_hours: int = 24
    embargo_hours: int = DEFAULT_EMBARGO_HOURS


def load_series() -> pd.DataFrame:
    """The scoreable universe: periods with both an actual and an ESO forecast.

    Restricting to comparable periods here is what makes FR-20's "identical
    periods" hold by construction. A period the ESO never forecast is excluded
    for every model, not only for the ESO — otherwise GridCast would be credited
    with periods its benchmark never had the chance to attempt.
    """
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT sp_start_utc, actual_gco2_kwh, eso_forecast_gco2_kwh, is_comparable
              FROM marts.fct_intensity_period
             ORDER BY sp_start_utc
        """)
        rows = cur.fetchall()

    frame = pd.DataFrame(rows)
    frame["sp_start_utc"] = pd.to_datetime(frame["sp_start_utc"], utc=True)
    return frame.set_index("sp_start_utc")


def run(config: BacktestConfig) -> pd.DataFrame:
    """Score every baseline and the ESO benchmark across rolling origins."""
    frame = load_series()
    actual = frame["actual_gco2_kwh"].astype(float)
    eso = frame["eso_forecast_gco2_kwh"].astype(float)
    comparable = frame["is_comparable"].astype(bool)
    observed = Observed(actual=actual)

    origins = pd.date_range(
        config.date_from, config.date_to, freq=f"{config.step_hours}h", tz="UTC"
    )
    embargo = timedelta(hours=config.embargo_hours)

    records: list[dict] = []
    profile = pd.Series(dtype=float)
    profile_month: tuple[int, int] | None = None

    for origin in origins:
        # Training data ends here, not at the origin. The gap is the embargo.
        train_until = origin - embargo

        month_key = (origin.year, origin.month)
        if month_key != profile_month:
            profile = build_climatology(observed, train_until)
            profile_month = month_key

        targets = pd.DatetimeIndex([origin + (h * PERIOD) for h in range(1, HORIZONS + 1)])
        present = targets.isin(actual.index)
        if not present.any():
            continue

        targets = targets[present]
        horizons = np.array([int((t - origin) / PERIOD) for t in targets])

        truth = actual.reindex(targets).to_numpy(dtype=float)
        usable = comparable.reindex(targets).fillna(False).to_numpy(dtype=bool) & ~np.isnan(truth)
        if not usable.any():
            continue

        predictions = {name: fn(observed, train_until, targets) for name, fn in BASELINES.items()}
        predictions["B3_climatology"] = climatology(profile, targets)
        # Not horizon-matched. See the module docstring.
        predictions["ESO_final"] = eso.reindex(targets).to_numpy(dtype=float)

        for name, predicted in predictions.items():
            valid = usable & ~np.isnan(predicted)
            if not valid.any():
                continue
            for h, a, p in zip(horizons[valid], truth[valid], predicted[valid], strict=True):
                records.append(
                    {"origin": origin, "model": name, "horizon": int(h), "actual": a, "pred": p}
                )

    return pd.DataFrame.from_records(records)


def summarise(scores: pd.DataFrame, scale: float) -> pd.DataFrame:
    """Aggregate to model x horizon group, always carrying the sample size.

    NFR-9 forbids displaying an accuracy figure without its horizon and sample
    size, and the cheapest way to comply is to make it impossible to obtain the
    number without the count beside it.
    """
    scores = scores.copy()
    scores["horizon_group"] = scores["horizon"].map(horizon_group)

    def agg(group: pd.DataFrame) -> pd.Series:
        a = group["actual"].to_numpy()
        p = group["pred"].to_numpy()
        return pd.Series(
            {
                "n": len(group),
                "mae": metrics.mae(a, p),
                "rmse": metrics.rmse(a, p),
                "bias": metrics.bias(a, p),
                "mase": metrics.mase(a, p, scale),
            }
        )

    return (
        scores.groupby(["model", "horizon_group"], as_index=False)
        .apply(agg, include_groups=False)
        .sort_values(["horizon_group", "mae"])
        .reset_index(drop=True)
    )


def persist(summary: pd.DataFrame, config: BacktestConfig, scale: float) -> uuid.UUID:
    """Write the summary to the backtest schema.

    Per-point scores are not stored. A full run produces well over a million of
    them, against 95 MB of headroom on a 512 MB project — and they are
    reproducible at will, because the harness is deterministic and committed.
    The aggregates are what anything downstream reads.
    """
    run_id = uuid.uuid4()
    settings = get_settings()

    with connect(url=settings.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO backtest.bt_run
                (bt_run_id, date_from, date_to, step_hours, embargo_hours,
                 mase_scale, code_commit)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                config.date_from,
                config.date_to,
                config.step_hours,
                config.embargo_hours,
                scale,
                settings.build_id,
            ),
        )
        for row in summary.itertuples(index=False):
            cur.execute(
                """
                INSERT INTO backtest.bt_score
                    (bt_run_id, model, horizon_group, n, mae, rmse, bias, mase)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    row.model,
                    row.horizon_group,
                    int(row.n),
                    float(row.mae),
                    float(row.rmse),
                    float(row.bias),
                    None if np.isnan(row.mase) else float(row.mase),
                ),
            )
    return run_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="date_from", default="2019-01-01")
    parser.add_argument("--to", dest="date_to", default=None)
    parser.add_argument("--step-hours", type=int, default=24)
    parser.add_argument("--embargo-hours", type=int, default=DEFAULT_EMBARGO_HOURS)
    parser.add_argument("--save", action="store_true", help="Persist the summary")
    args = parser.parse_args()

    config = BacktestConfig(
        date_from=datetime.fromisoformat(args.date_from).replace(tzinfo=UTC),
        date_to=(
            datetime.fromisoformat(args.date_to).replace(tzinfo=UTC)
            if args.date_to
            else datetime.now(UTC) - timedelta(days=3)
        ),
        step_hours=args.step_hours,
        embargo_hours=args.embargo_hours,
    )

    print(
        f"rolling-origin backtest  {config.date_from:%Y-%m-%d} to {config.date_to:%Y-%m-%d}"
        f"  step {config.step_hours}h  embargo {config.embargo_hours}h"
    )

    scores = run(config)
    if scores.empty:
        print("no scoreable forecasts produced")
        return 1

    frame = load_series()
    scale = metrics.seasonal_naive_scale(frame["actual_gco2_kwh"].astype(float).to_numpy())

    summary = summarise(scores, scale)
    print(f"\n{len(scores):,} scored forecast points | MASE scale (seasonal naive) = {scale:.2f}\n")
    print(summary.to_string(index=False, float_format=lambda v: f"{v:9.3f}"))

    print(
        "\nESO_final is NOT horizon-matched: the stored value is the ESO's final "
        "\nforecast, not a 48-hour-ahead one, so this comparison flatters it."
    )

    if args.save:
        run_id = persist(summary, config, scale)
        print(f"\nsaved as backtest run {run_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
