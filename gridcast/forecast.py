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
from pathlib import Path

import numpy as np
import pandas as pd

from gridcast.baselines import (
    PERIOD,
    PERIODS_PER_DAY,
    SEASONAL_WALKBACK_STEPS,
    Observed,
)
from gridcast.calibrate import STALE_AFTER, load_calibration
from gridcast.config import get_settings
from gridcast.db import connect
from gridcast.runlog import RunContext
from gridcast.usage import record_on_exit

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
    "G2_gbm_v1": "hist_gradient_boosting",
    "ESO_published": "external_benchmark",
}

# The trained artefact, committed alongside the code that produced it. Absent
# artefact means G2 simply does not issue this run — a missing model must not
# stop the champion and the benchmark from being recorded, because a gap in
# their series is a gap in the evidence.
G2_ARTEFACT = Path(__file__).resolve().parent.parent / "models" / "G2_gbm_v1.joblib"

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


# How much observed history an issuing run loads.
#
# This one is not purely a transfer saving: the series feeds
# empirical_error_quantiles, so the window is a modelling input and narrowing
# it changes the published intervals. It is set deliberately.
#
# A year gives 17,520 samples per horizon band, which is far past the point
# where a quantile estimate stops moving. The archive reaches back to 2018, and
# the grid of 2018 is not the grid being forecast — its error distribution
# belongs to a system with substantially more coal in it. Calibrating today's
# intervals on it is not more information, it is older information.
#
# Set to None to restore the full archive.
ERROR_HISTORY_DAYS: int | None = 365

# How much observed history an ISSUING run loads.
#
# Distinct from ERROR_HISTORY_DAYS, which is a modelling window and stays a
# year. This one is a reach: the seasonal naive steps back at most
# SEASONAL_WALKBACK_STEPS whole days before returning NaN, so nothing older can
# affect a forecast. Loading a year to consult a fortnight of it is what made
# issuing the most expensive recurring read in the project — 17,520 rows every
# thirty minutes, on a plan metered in bytes read.
#
# The calibration that genuinely needs the year is computed once a day by
# gridcast.calibrate and read back as sixteen rows.
#
# Derived with a week of slack, so a gap has to swallow the entire walk-back
# plus seven days before this changes an answer the unbounded read would have
# given.
ISSUING_HISTORY_DAYS = SEASONAL_WALKBACK_STEPS + 7


def load_actuals(since: datetime | None = None) -> pd.Series:
    """The observed series. Matured periods only.

    Forecasting from an unmatured actual would mean building on a number the
    upstream may still revise, and every forecast issued from it would inherit
    the revision without any record of why it moved.
    """
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT sp_start_utc, actual_gco2_kwh
              FROM marts.fct_intensity_period
             WHERE actual_gco2_kwh IS NOT NULL
               AND is_matured
               AND (%s::timestamptz IS NULL OR sp_start_utc >= %s)
             ORDER BY sp_start_utc
            """,
            (since, since),
        )
        rows = cur.fetchall()

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.Series(dtype=float)
    index = pd.to_datetime(frame["sp_start_utc"], utc=True)
    return pd.Series(frame["actual_gco2_kwh"].astype(float).to_numpy(), index=index)


def error_band_lag(high: int) -> int:
    """Periods a band must step back when forecasting.

    Horizon 96 needs the value from two days prior, horizon 6 only one. Shared
    with the calibration job so the sample count it records is the count this
    function actually used — the same expression written twice is the usual way
    a stored `n` stops describing the estimate beside it.
    """
    days_back = max(1, int(np.ceil(high / PERIODS_PER_DAY)))
    return days_back * PERIODS_PER_DAY


def empirical_error_quantiles(actual: pd.Series) -> dict[tuple[int, int], dict[str, float]]:
    """The model's own historical error distribution, per horizon band.

    Computed from the seasonal-naive error on history: for each band, the
    spread of (actual - same period one day earlier). Long horizons must step
    back further than one day, so their errors are wider — which the bands
    capture without needing 96 separate estimates.

    Reading a year of actuals to produce sixteen numbers is expensive enough
    that issuing no longer does it; gridcast.calibrate runs this daily and
    stores the result. This remains the only implementation, and the stored
    values are exactly its output.
    """
    if len(actual) <= PERIODS_PER_DAY * 8:
        return {}

    values = actual.to_numpy()
    quantiles: dict[tuple[int, int], dict[str, float]] = {}

    for low, high in ERROR_BANDS:
        lag = error_band_lag(high)
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


def load_g2():
    """Load the trained model, or None if it is not present.

    Imports scikit-learn lazily. This module is only ever run by the pipeline,
    but keeping the import inside the function means an environment without a
    modelling stack can still issue the baselines rather than failing outright.
    """
    if not G2_ARTEFACT.exists():
        return None
    import joblib

    return joblib.load(G2_ARTEFACT)


def build_g2_forecast(bundle, run_at, anchor, targets):
    """G2's point forecast and quantile interval for each target.

    Features are built through exactly the same function used in training, at
    the same embargo. A separate serving-time feature path is the classic way a
    model that scored well offline quietly degrades in production, because the
    two implementations drift and nothing compares them.
    """
    from gridcast.features import (
        SERVING_HISTORY_DAYS,
        WEATHER_TRAILING_DAYS,
        assert_weather_reaches,
        build_features,
        load_intensity_history,
        load_mix_history,
        load_weather_forecast,
    )

    # Issuing reaches 168 hours back. Loading the rest of the archive 48 times
    # a day is what spent a month of the database's transfer allowance in under
    # a week; see SERVING_HISTORY_DAYS.
    since = anchor - timedelta(days=SERVING_HISTORY_DAYS)

    intensity = load_intensity_history(since)
    mix = load_mix_history(since)

    # Weather on its own, narrower window. build_features reads it at the
    # targets and across the 48 periods before the anchor, and nowhere between
    # — so the rows in between were being fetched only to be dropped by the
    # reindex. Bounded forward at the furthest target for the same reason:
    # a row beyond it cannot reach any feature.
    #
    # THE LIVE FORECAST, not the vintage. Issuing read the vintage relation
    # until 2026-09-04, which holds no row for an hour that has not happened
    # yet: first that made every forward weather feature NaN and issued anyway,
    # then it made the frame empty and stopped G2 issuing at all.
    weather = load_weather_forecast(
        anchor - timedelta(days=WEATHER_TRAILING_DAYS),
        until=max(targets),
    )
    assert_weather_reaches(weather, targets)

    # Partial forward coverage is legitimate — the upstream forecast is finite
    # and a run near its edge can outrun it — but it is not silent. The tail
    # horizons carry NaN weather, and a leaderboard that degrades at H4 for a
    # week is worth being able to explain from the logs.
    furthest_weather = weather.index.max()
    if furthest_weather < max(targets):
        print(
            f"  G2_gbm_v1               weather stops at "
            f"{furthest_weather:%Y-%m-%d %H:%M}Z, short of the furthest target "
            f"at {max(targets):%Y-%m-%d %H:%M}Z — tail horizons issue with NaN weather"
        )

    frame = build_features(
        run_at, targets, intensity=intensity, mix=mix, weather=weather, anchor=anchor
    )
    matrix = frame[bundle["features"]].to_numpy(dtype=float)

    point = bundle["model"].predict(matrix)
    quantiles = {
        name: q_model.predict(matrix) for name, q_model in bundle["quantile_models"].items()
    }

    # Independently fitted quantiles can cross. Sorting is the standard remedy;
    # without it the database CHECK would reject the row outright rather than
    # the interval merely being wrong.
    stacked = np.vstack([quantiles[k] for k in ("q025", "q10", "q90", "q975")])
    stacked.sort(axis=0)

    # Apply the conformal widening measured at training time.
    #
    # Without it the live intervals would be the raw quantile output, which
    # covered 59-63% against a nominal 80% — the exact failure the training run
    # was changed to fix. Serving uncalibrated intervals while reporting
    # calibrated ones in the model card would be worse than not shipping
    # intervals at all.
    conformal = bundle.get("conformal", {})
    widen_80 = float(conformal.get("q10|q90", 0.0))
    widen_95 = float(conformal.get("q025|q975", 0.0))
    stacked[0] -= widen_95  # q025
    stacked[1] -= widen_80  # q10
    stacked[2] += widen_80  # q90
    stacked[3] += widen_95  # q975
    stacked.sort(axis=0)  # widening cannot reorder, but re-assert it

    rows = []
    for i, target in enumerate(targets):
        rows.append(
            ForecastRow(
                target=target,
                horizon=int((target - anchor) / PERIOD),
                point=float(point[i]),
                quantiles={
                    "q025": float(stacked[0][i]),
                    "q10": float(stacked[1][i]),
                    "q90": float(stacked[2][i]),
                    "q975": float(stacked[3][i]),
                },
                feature_hash=hashlib.sha256(
                    frame.iloc[i][bundle["features"]].to_json().encode()
                ).digest(),
            )
        )
    return rows


def record_challenger_failure(
    version: str, run_id: uuid.UUID, run_at: datetime, exc: BaseException
) -> None:
    """Put a caught challenger failure in the run log, where it can be seen.

    Catching it is right: a challenger that cannot build its features must not
    stop the champion and the benchmark being recorded. Catching it into a
    print is what made G2's three-week absence invisible — stdout belongs to
    the runner and expires with it, while landing.run_log is what the status
    page publishes and what anyone outside this process can read.

    Exactly one row per model per run, either way. A model that builds is
    recorded by the write loop; one that does not is recorded here, and the two
    paths cannot both fire.

    Swallows its own failure. This runs after something has already gone wrong,
    and the likeliest reason the row cannot be written — an unreachable
    database — is also a likely reason the forecast failed. Losing the log
    entry is bad; losing the champion's forecast to a logging error is worse.
    """
    try:
        with RunContext(
            run_id,
            source=version,
            job="forecast",
            window_from=run_at,
            window_to=run_at + timedelta(hours=48),
        ) as run:
            run.failure = exc
    except Exception as log_exc:  # noqa: BLE001
        print(f"  {version:<24} could not record the failure: {type(log_exc).__name__}")


NOTES = {
    "B1_seasonal_naive_q_v1": (
        "M5 opening champion. A baseline on purpose: the milestone's deliverable "
        "is the loop, and a model whose behaviour is fully understood makes the "
        "first live scores interpretable."
    ),
    "B0_persistence_v1": (
        "Reference baseline. Establishes that a model has learned anything at all."
    ),
    "G2_gbm_v1": (
        "Gradient boosting on 39 point-in-time features, no ESO input - the fair "
        "competitor. Out-of-sample MASE 0.46 to 0.55 against a best baseline of "
        "1.03 to 1.38. Issued as a challenger; promotion is decided only by the "
        "pre-registered rule."
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
    # Registered before any work, so a run that dies mid-read still
    # accounts for what it spent (NFR-13).
    record_on_exit("forecast")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()

    settings = get_settings()
    print(f"database: {settings.database_url.split('@')[-1].split('?')[0] or 'NOT CONFIGURED'}")

    actual = load_actuals(datetime.now(UTC) - timedelta(days=ISSUING_HISTORY_DAYS))
    if actual.empty:
        print("no matured actuals available; nothing to forecast from")
        return 1

    observed = Observed(actual=actual)

    # The intervals come from the stored calibration, not from this run.
    #
    # The fallback computes it here, which is expensive and correct — a first
    # run against a fresh database has nothing stored, and issuing point
    # forecasts with no intervals rather than reading a year once would be
    # choosing the wrong thing to protect.
    error_quantiles, computed_at = load_calibration()
    if error_quantiles:
        age = datetime.now(UTC) - computed_at
        if age > STALE_AFTER:
            print(
                f"::warning title=Calibration::Intervals are calibrated from "
                f"{computed_at:%Y-%m-%d %H:%M}Z, {age.days} days old. The daily "
                "calibration job has not run. Forecasts are still issued — a "
                "calibration fitted on a year is not wrong after a few days — "
                "but it is no longer current."
            )
        else:
            hours = age.total_seconds() / 3600
            print(f"calibration from {computed_at:%Y-%m-%d %H:%M}Z ({hours:.1f} h old)")
    else:
        print("no stored calibration; computing once from history")
        history = load_actuals(
            datetime.now(UTC) - timedelta(days=ERROR_HISTORY_DAYS) if ERROR_HISTORY_DAYS else None
        )
        error_quantiles = empirical_error_quantiles(history)
        if not error_quantiles:
            print(
                "::warning title=Calibration::Not enough history to calibrate "
                "intervals. Point forecasts will be issued without them."
            )

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

    bundle = load_g2()
    if bundle is None:
        print("  G2_gbm_v1               artefact not found — not issued this run")
    else:
        try:
            issued["G2_gbm_v1"] = build_g2_forecast(
                bundle, run_at, current_period, pd.DatetimeIndex([r.target for r in rows])
            )
        except Exception as exc:  # noqa: BLE001
            # A challenger failing must never stop the champion or the benchmark
            # being recorded. Their series are the evidence; a hole in them
            # cannot be refilled later because the moment has passed.
            print(f"  G2_gbm_v1               FAILED {type(exc).__name__}: {exc}")
            record_challenger_failure("G2_gbm_v1", run_id, run_at, exc)

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
