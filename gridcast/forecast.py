"""Issue forecasts into the append-only register (SRS FR-13, FR-14).

    python -m gridcast.forecast                 # issue for the champion
    python -m gridcast.forecast --dry-run       # print, write nothing

Runs every pipeline execution. Each run forecasts every settlement period up to
48 hours ahead and writes the result to `register.reg_forecast_point`, which the
application role can INSERT into and cannot UPDATE or DELETE.

WHY THE CHAMPION AT M5 IS A BASELINE.

The champion here is seasonal naive with empirical quantile intervals. That is
deliberate, not a placeholder left by accident.

M5's deliverable is the *loop* — issue, seal, score, publish — not the model.
Opening the scoreboard with a simple model whose behaviour is completely
understood means that when a real model arrives at M6 there is already a live,
honestly scored incumbent to beat, and any improvement is measured rather than
assumed. Starting with a sophisticated champion would mean the first live
numbers came from a model nobody had watched run.

WHAT THIS JOB DOES NOT DO.

It does not train. Training happens offline in scheduled jobs and the serving
API never imports a modelling stack (NFR-6). This job loads a series, applies a
rule, and writes rows.

It also does not decide whether the forecast is any good. That is the scoring
job's business, and keeping the two apart is what stops a model from grading
its own homework.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from gridcast.baselines import PERIOD, PERIODS_PER_DAY, Observed
from gridcast.config import get_settings
from gridcast.db import connect
from gridcast.runlog import RunContext

HORIZONS = 96

# Every model issued each run. All share one run_at, so FR-20's requirement
# that models be scored on identical periods holds by construction instead of
# being enforced by a filter somebody could forget.
#
# ESO_published is not a competitor we built. It is the ESO's own forecast,
# recorded at the horizon we actually received it — which is the one thing a
# backtest can never do (see load_eso_forecast).
CHAMPION_VERSION = "B1_seasonal_naive_q_v1"
CHAMPION_FAMILY = "seasonal_naive"

ISSUED_MODELS: dict[str, str] = {
    "B1_seasonal_naive_q_v1": "seasonal_naive",
    "B0_persistence_v1": "persistence",
    "ESO_published": "external_benchmark",
}

# Quantiles are empirical: the distribution of this model's own historical
# errors at each horizon, rather than a normal approximation. Intensity errors
# are skewed and heteroscedastic — wider on windy days, tighter overnight — so a
# symmetric interval would misstate risk in both directions at once.
QUANTILE_LEVELS = {"q025": 0.025, "q10": 0.10, "q90": 0.90, "q975": 0.975}

# Errors are pooled into horizon bands rather than estimated per half-hour step.
# 96 separate distributions from a few months of live data would each be fitted
# on too little to be stable, and the error distribution genuinely does not
# change much between, say, horizon 51 and 52.
ERROR_BANDS = [(1, 6), (7, 24), (25, 48), (49, 96)]


def band_of(horizon: int) -> tuple[int, int]:
    for low, high in ERROR_BANDS:
        if low <= horizon <= high:
            return low, high
    return ERROR_BANDS[-1]


@dataclass(frozen=True)
class ForecastRow:
    target: datetime
    horizon: int
    point: float
    quantiles: dict[str, float]
    feature_hash: bytes


def load_eso_forecast(anchor: datetime) -> dict[datetime, float]:
    """The ESO's published forecast for future periods, as we hold it right now.

    THIS IS THE POINT OF THE LIVE LOOP.

    In backfilled history the ESO forecast is not horizon-matched: the value
    stored against a 2019 period is their final near-term forecast, because they
    revise continuously as the horizon shortens. Measured at M4 — of 46 future
    periods held, 33 had been revised within two hours. A backtest therefore
    compares GridCast at 48 hours against the ESO at something much shorter, and
    a loss there is close to uninformative.

    Here it is different. These rows were fetched minutes ago for periods that
    have not happened. Writing them into the register at this issue time, beside
    our own forecast for the same periods, produces the first genuinely
    like-for-like comparison this project can make.

    The ESO is not being asked to compete. Their forecast is simply being
    recorded with the horizon at which we actually received it.
    """
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (sp_start_utc)
                   sp_start_utc,
                   (payload->'intensity'->>'forecast')::numeric AS eso_forecast
              FROM landing.lnd_ci_intensity
             WHERE sp_start_utc > %s
               AND payload->'intensity'->>'forecast' IS NOT NULL
             ORDER BY sp_start_utc, fetched_at_utc DESC, landing_id DESC
            """,
            (anchor,),
        )
        return {row["sp_start_utc"]: float(row["eso_forecast"]) for row in cur.fetchall()}


def load_actuals() -> pd.Series:
    """The observed series. Matured periods only.

    Forecasting from an unmatured actual would mean building on a number the
    upstream may still revise, and every forecast issued from it would inherit
    the revision without any record of why it moved.
    """
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT sp_start_utc, actual_gco2_kwh
              FROM marts.fct_intensity_period
             WHERE actual_gco2_kwh IS NOT NULL
               AND is_matured
             ORDER BY sp_start_utc
        """)
        rows = cur.fetchall()

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.Series(dtype=float)
    index = pd.to_datetime(frame["sp_start_utc"], utc=True)
    return pd.Series(frame["actual_gco2_kwh"].astype(float).to_numpy(), index=index)


def empirical_error_quantiles(actual: pd.Series) -> dict[tuple[int, int], dict[str, float]]:
    """The model's own historical error distribution, per horizon band.

    Computed from the seasonal-naive error on history: for each band, the
    spread of (actual - same period one day earlier). Long horizons must step
    back further than one day, so their errors are wider — which the bands
    capture without needing 96 separate estimates.
    """
    if len(actual) <= PERIODS_PER_DAY * 8:
        return {}

    values = actual.to_numpy()
    quantiles: dict[tuple[int, int], dict[str, float]] = {}

    for low, high in ERROR_BANDS:
        # Days back this band must reach when forecasting: horizon 96 needs the
        # value from two days prior, horizon 6 only one.
        days_back = max(1, int(np.ceil(high / PERIODS_PER_DAY)))
        lag = days_back * PERIODS_PER_DAY
        errors = values[lag:] - values[:-lag]
        quantiles[(low, high)] = {
            name: float(np.quantile(errors, level)) for name, level in QUANTILE_LEVELS.items()
        }

    return quantiles


def build_forecast(
    observed: Observed,
    run_at: datetime,
    error_quantiles: dict[tuple[int, int], dict[str, float]],
    *,
    anchor: datetime | None = None,
) -> list[ForecastRow]:
    """Forecast every horizon from 1 to 96, using only observable values.

    `anchor` is the settlement period containing the issue time; horizons are
    counted from it so they stay whole numbers while `run_at` remains the true,
    unrounded instant of computation.
    """
    anchor = anchor or run_at
    rows: list[ForecastRow] = []

    for horizon in range(1, HORIZONS + 1):
        target = anchor + horizon * PERIOD
        if target <= run_at:
            # Cannot forecast a period that has already begun. The database
            # CHECK enforces this too; catching it here keeps the constraint
            # from being the first thing that notices.
            continue
        point = observed.most_recent_seasonal(target, run_at, PERIODS_PER_DAY)
        if not np.isfinite(point):
            continue

        spread = error_quantiles.get(band_of(horizon), {})
        quantiles = {name: point + offset for name, offset in spread.items()}

        # The exact inputs that produced this number, hashed. It is what makes a
        # disputed forecast resolvable years later: the feature vector can be
        # recomputed from the warehouse's vintage history and compared.
        feature_payload = json.dumps(
            {
                "rule": "most_recent_knowable_same_period_of_day",
                "season_periods": PERIODS_PER_DAY,
                "run_at": run_at.isoformat(),
                "target": target.isoformat(),
                "source_value": point,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        rows.append(
            ForecastRow(
                target=target,
                horizon=horizon,
                point=point,
                quantiles=quantiles,
                feature_hash=hashlib.sha256(feature_payload.encode()).digest(),
            )
        )

    return rows


def row_hash(model_version: str, run_at: datetime, row: ForecastRow) -> bytes:
    """The unit of the monthly integrity seal."""
    payload = json.dumps(
        {
            "model_version": model_version,
            "run_at": run_at.isoformat(),
            "target": row.target.isoformat(),
            "horizon": row.horizon,
            "point": round(row.point, 3),
            "q": {k: round(v, 3) for k, v in sorted(row.quantiles.items())},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).digest()


NOTES = {
    "B1_seasonal_naive_q_v1": (
        "M5 opening champion. A baseline on purpose: the milestone's deliverable "
        "is the loop, and a model whose behaviour is fully understood makes the "
        "first live scores interpretable."
    ),
    "B0_persistence_v1": (
        "Reference baseline. Establishes that a model has learned anything at all."
    ),
    "ESO_published": (
        "National Grid ESO's published forecast, recorded at the horizon we "
        "received it. Not built here and not competing — it is the benchmark, "
        "and this is the only place it can be compared like for like."
    ),
}


def ensure_models_registered(commit: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        for version, family in ISSUED_MODELS.items():
            role = "champion" if version == CHAMPION_VERSION else "challenger"
            cur.execute(
                """
                INSERT INTO register.reg_model_version
                    (model_version, model_family, code_commit, role, uses_eso_forecast, notes)
                VALUES (%s, %s, %s, %s, false, %s)
                ON CONFLICT (model_version) DO NOTHING
                """,
                (version, family, commit, role, NOTES[version]),
            )


def write_forecasts(
    model_version: str, rows: list[ForecastRow], run_at: datetime, run_id: uuid.UUID
) -> int:
    settings = get_settings()
    written = 0

    with connect() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO register.reg_forecast_point
                    (forecast_id, model_version, run_id, run_at_utc, target_sp_start_utc,
                     horizon_periods, point_gco2_kwh,
                     q025_gco2_kwh, q10_gco2_kwh, q90_gco2_kwh, q975_gco2_kwh,
                     code_commit, feature_snapshot_hash, row_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_version, run_at_utc, target_sp_start_utc) DO NOTHING
                """,
                (
                    uuid.uuid4(),
                    model_version,
                    run_id,
                    run_at,
                    row.target,
                    row.horizon,
                    round(row.point, 3),
                    row.quantiles.get("q025"),
                    row.quantiles.get("q10"),
                    row.quantiles.get("q90"),
                    row.quantiles.get("q975"),
                    settings.build_id,
                    row.feature_hash,
                    row_hash(model_version, run_at, row),
                ),
            )
            written += cur.rowcount

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()

    settings = get_settings()
    print(f"database: {settings.database_url.split('@')[-1].split('?')[0] or 'NOT CONFIGURED'}")

    actual = load_actuals()
    if actual.empty:
        print("no matured actuals available; nothing to forecast from")
        return 1

    observed = Observed(actual=actual)
    error_quantiles = empirical_error_quantiles(actual)

    # The issue time is the true instant of computation, never rounded.
    #
    # Rounding it down to the settlement period would make horizons tidy whole
    # numbers, and would backdate every forecast by up to 29 minutes — claiming
    # more lead time than the model actually had. In a project whose entire
    # premise is that a forecast was published before its outcome existed, that
    # is the one direction the error must never go.
    #
    # Horizons stay integers anyway by counting periods from the period we are
    # currently *in*: horizon h targets the h-th period boundary after it. The
    # first horizon is therefore somewhat less than 30 minutes ahead, which
    # understates the lead time rather than overstating it.
    run_at = datetime.now(UTC)
    current_period = run_at.replace(minute=0 if run_at.minute < 30 else 30, second=0, microsecond=0)

    rows = build_forecast(observed, run_at, error_quantiles, anchor=current_period)
    print(f"issue time {run_at:%Y-%m-%d %H:%M}Z | {len(rows)} horizons | model {CHAMPION_VERSION}")

    if not rows:
        print("no forecastable horizons")
        return 1

    if args.dry_run:
        for row in rows[:5]:
            interval = ""
            if "q10" in row.quantiles:
                interval = f"  80% [{row.quantiles['q10']:.0f}, {row.quantiles['q90']:.0f}]"
            print(f"  h{row.horizon:<3} {row.target:%Y-%m-%d %H:%M}  {row.point:7.1f}{interval}")
        print(f"  ... {len(rows) - 5} more (dry run, nothing written)")
        return 0

    run_id = uuid.uuid4()
    ensure_models_registered(settings.build_id)

    eso = load_eso_forecast(current_period)
    eso_rows = [
        ForecastRow(
            target=row.target,
            horizon=row.horizon,
            point=eso[row.target],
            quantiles={},
            feature_hash=hashlib.sha256(
                f"eso_published|{row.target.isoformat()}".encode()
            ).digest(),
        )
        for row in rows
        if row.target in eso
    ]

    persistence_point = observed.last_known(run_at)
    persistence_rows = [
        ForecastRow(
            target=row.target,
            horizon=row.horizon,
            point=persistence_point,
            quantiles={},
            feature_hash=hashlib.sha256(
                f"persistence|{run_at.isoformat()}|{persistence_point}".encode()
            ).digest(),
        )
        for row in rows
    ]

    issued = {
        CHAMPION_VERSION: rows,
        "B0_persistence_v1": persistence_rows,
        "ESO_published": eso_rows,
    }

    total = 0
    for version, model_rows in issued.items():
        if not model_rows:
            # An empty set is worth saying out loud. A silently absent model
            # would leave a hole in the leaderboard that looks like the model
            # performing badly rather than never having forecast.
            print(f"  {version:<24} no forecastable horizons — nothing issued")
            continue

        with RunContext(
            run_id,
            source=version,
            job="forecast",
            window_from=run_at,
            window_to=run_at + timedelta(hours=48),
        ) as run:
            run.rows_read = len(model_rows)
            run.rows_written = write_forecasts(version, model_rows, run_at, run_id)
            total += run.rows_written
        print(f"  {version:<24} {run.rows_written:>3} forecast(s)")

    print(f"wrote {total} forecast(s) to the register at {run_at:%Y-%m-%d %H:%M:%S}Z")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
