"""Point-in-time feature construction (SRS FR-15, design 8.2 and 8.4).

Every feature is expressed relative to the **issue time**, never to the target
period. "Intensity 24 hours before the target" is not a legal feature at a
48-hour horizon, because that period has not happened yet when the forecast is
made. "Intensity 24 hours before issue time" is.

That constraint is what makes leakage structurally difficult rather than merely
discouraged, and it is the reason this project uses a direct multi-horizon
formulation: one model, all 96 horizons, with the horizon itself as a feature.

Three sources are barred outright and the bans are enforced, not remembered:

  * `stg_om_archive` — reanalysis weather actuals. What the weather turned out
    to be. Production will never have it at issue time.
  * `fct_demand_current` — demand resolved to its latest revision rather than
    the vintage known at issue time.
  * `intensity.index` — the ESO band, which encodes the publication year as much
    as the intensity (M2 findings B03/B04).

A model trained on any of them backtests beautifully and fails in production,
and the gap stays invisible until the live scoreboard opens.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from gridcast.baselines import LeakageError
from gridcast.config import get_settings
from gridcast.db import connect

PERIOD = timedelta(minutes=30)
PERIODS_PER_DAY = 48

# Weather locations that survived D-3. scotland_north correlates 0.4285 with
# national wind share against a floor of 0.5 fixed before the numbers were seen.
from gridcast.sources.open_meteo import FEATURE_LOCATIONS  # noqa: E402

# Lags, in hours before the ISSUE TIME. Not before the target — that is the
# whole point.
INTENSITY_LAG_HOURS = (0, 24, 48, 168)
ROLLING_WINDOWS_HOURS = (24, 168)

# The furthest back build_features can reach. Derived, not typed in, so a new
# lag cannot silently outrun the window that serving loads.
FEATURE_REACH_HOURS = max(*INTENSITY_LAG_HOURS, *ROLLING_WINDOWS_HOURS)

# How much history an ISSUING run loads. Training and backtesting still read
# everything — they are measuring the past, so they need it.
#
# A forecast does not. It reaches 168 hours back and no further, so the rest of
# the archive is fetched, parsed into a DataFrame, and dropped. That cost every
# issue: three unbounded SELECTs against the marts, 48 times a day, against a
# database whose free tier meters bytes read. It exhausted a month of transfer
# allowance in under a week and took the whole site down with it, which is a
# lot of damage for rows nothing was going to look at.
#
# More than the reach, not equal to it: the lag takes the last row at or before
# its stamp, so a gap spanning the boundary would leave the feature NaN where an
# unbounded read found a real value. A week of slack past the reach absorbs any
# outage worth forecasting through.
#
# Derived rather than typed, so a new lag cannot silently outrun the window that
# serving loads — the failure it would cause is a NaN feature at issue time,
# which the model would consume without complaint.
SERVING_HISTORY_DAYS = -(-FEATURE_REACH_HOURS // 24) + 7

# Weather is loaded on a different window because it is used differently.
#
# build_features consults weather at exactly two places: the TARGET periods,
# via reindex, and the 48 periods at or before the issue time, for the wind
# ramp. Rows between those two spans are fetched, parsed, and then dropped by
# the reindex — provably dead, not merely unlikely to matter. Loading three
# days of trailing weather instead of thirty removes them and changes no
# feature.
#
# Three rather than one: the ramp takes the last 48 rows at or before the
# anchor, and under a gap that span reaches back further than 24 hours.
WEATHER_TRAILING_DAYS = 3


# The publication lag assumed for backfilled rows. PROVISIONAL.
#
# A backfilled row's fetched_at_utc is the instant the backfill ran, not the
# instant the value became available — so for history it says "known today",
# and a guard applied literally rejects the entire past. The first attempt to
# train did exactly that: every origin raised LeakageError and the run reported
# "insufficient data", which diagnosed the data rather than the code.
#
# Design 8.3 resolves it by reconstructing knowability as
# sp_start_utc + measured_publication_lag. The measurement for ESO actuals
# requires forward observation (D-1) and does not exist yet; 24 hours is used
# meanwhile, matching the maturity threshold so the two cannot disagree.
#
# It is deliberately generous. Assuming a value took LONGER to publish than it
# did makes features more conservative, never less — the error runs towards
# withholding information from the model, which is the safe direction.
RECONSTRUCTED_LAG = timedelta(hours=24)


def load_intensity_history(since: datetime | None = None) -> pd.DataFrame:
    """Matured actuals with the instant each became knowable to us.

    `knowable_effective_utc` is the true fetch time for rows observed live, and
    a reconstructed estimate for rows loaded by backfill. Which is which stays
    visible in `knowable_is_reconstructed`, because results built on the two
    are reported in separate columns and never pooled (design 8.3).

    `since` bounds the read for callers that only need recent history. Default
    None keeps the full archive, because training and backtesting measure it.
    """
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT sp_start_utc, actual_gco2_kwh, knowable_at_utc, knowable_is_reconstructed
              FROM marts.fct_intensity_period
             WHERE actual_gco2_kwh IS NOT NULL
               AND (%s::timestamptz IS NULL OR sp_start_utc >= %s)
             ORDER BY sp_start_utc
            """,
            (since, since),
        )
        rows = cur.fetchall()

    frame = pd.DataFrame(rows)
    frame["sp_start_utc"] = pd.to_datetime(frame["sp_start_utc"], utc=True)
    frame["knowable_at_utc"] = pd.to_datetime(frame["knowable_at_utc"], utc=True)
    frame["actual_gco2_kwh"] = frame["actual_gco2_kwh"].astype(float)
    frame = frame.set_index("sp_start_utc")

    reconstructed = frame.index + RECONSTRUCTED_LAG
    frame["knowable_effective_utc"] = np.where(
        frame["knowable_is_reconstructed"].astype(bool),
        reconstructed,
        frame["knowable_at_utc"],
    )
    frame["knowable_effective_utc"] = pd.to_datetime(frame["knowable_effective_utc"], utc=True)
    return frame


def load_mix_history(since: datetime | None = None) -> pd.DataFrame:
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT sp_start_utc, wind_perc, solar_perc, low_carbon_perc, knowable_at_utc
              FROM marts.fct_mix_wide
             WHERE (%s::timestamptz IS NULL OR sp_start_utc >= %s)
             ORDER BY sp_start_utc
            """,
            (since, since),
        )
        rows = cur.fetchall()

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["sp_start_utc"] = pd.to_datetime(frame["sp_start_utc"], utc=True)
    for column in ("wind_perc", "solar_perc", "low_carbon_perc"):
        frame[column] = frame[column].astype(float)
    return frame.set_index("sp_start_utc")


def load_weather_history(
    since: datetime | None = None, until: datetime | None = None
) -> pd.DataFrame:
    """Weather from the FORECAST VINTAGE, pivoted wide by location.

    Drawn from `fct_weather_period`, which reads the materialised
    `fct_weather_hour` table — typed weather extracted from `lnd_om_vintage`.
    Never the archive.

    `until` bounds the read forward for issuing runs, which need weather only
    as far as their furthest target. Training passes neither bound and reads
    everything, as it must.
    """
    settings = get_settings()
    locations = tuple(FEATURE_LOCATIONS)
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT sp_start_utc, location_id,
                   wind_speed_100m_kmh, temperature_2m_c, shortwave_radiation_wm2
              FROM marts.fct_weather_period
             WHERE location_id = ANY(%s)
               AND (%s::timestamptz IS NULL OR sp_start_utc >= %s)
               AND (%s::timestamptz IS NULL OR sp_start_utc <= %s)
             ORDER BY sp_start_utc
            """,
            (list(locations), since, since, until, until),
        )
        rows = cur.fetchall()

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    frame["sp_start_utc"] = pd.to_datetime(frame["sp_start_utc"], utc=True)
    wide = frame.pivot_table(
        index="sp_start_utc",
        columns="location_id",
        values=["wind_speed_100m_kmh", "temperature_2m_c", "shortwave_radiation_wm2"],
        aggfunc="first",
    )
    wide.columns = [f"{measure}__{location}" for measure, location in wide.columns]
    return wide


def calendar_features(targets: pd.DatetimeIndex) -> pd.DataFrame:
    """Attributes of the TARGET period. These are always knowable.

    A calendar is the one thing a forecaster genuinely knows about the future:
    what day it will be, and where in the daily cycle. Encoded as sine and
    cosine pairs so that midnight is adjacent to 23:30 rather than 48 units
    away — a model given a raw period number has to learn that discontinuity
    from data, and never quite does.
    """
    period_of_day = targets.hour * 2 + (targets.minute >= 30).astype(int)
    day_of_year = targets.dayofyear

    return pd.DataFrame(
        {
            "sin_period_of_day": np.sin(2 * np.pi * period_of_day / PERIODS_PER_DAY),
            "cos_period_of_day": np.cos(2 * np.pi * period_of_day / PERIODS_PER_DAY),
            "sin_day_of_year": np.sin(2 * np.pi * day_of_year / 365.25),
            "cos_day_of_year": np.cos(2 * np.pi * day_of_year / 365.25),
            "day_of_week": targets.dayofweek,
            "is_weekend": (targets.dayofweek >= 5).astype(int),
        },
        index=targets,
    )


def build_features(
    run_at: datetime,
    targets: pd.DatetimeIndex,
    *,
    intensity: pd.DataFrame,
    mix: pd.DataFrame,
    weather: pd.DataFrame,
    anchor: datetime | None = None,
) -> pd.DataFrame:
    """Features for a set of targets, as knowable at `run_at`.

    Raises LeakageError if any input row could not have been held at run_at.
    """
    anchor = anchor or run_at

    # THE GUARD. Everything downstream is built only from these rows.
    #
    # knowable_effective_utc where present, knowable_at_utc otherwise. A frame
    # without either cannot be checked and must not silently pass — that is what
    # assert_knowable exists to refuse.
    if "knowable_effective_utc" not in intensity.columns:
        intensity = intensity.assign(knowable_effective_utc=intensity["knowable_at_utc"])

    observable = intensity.loc[
        (intensity.index <= anchor) & (intensity["knowable_effective_utc"] <= run_at)
    ]
    if observable.empty:
        raise LeakageError(f"no intensity observable at {run_at:%Y-%m-%d %H:%M}")

    series = observable["actual_gco2_kwh"]
    frame = calendar_features(targets)
    frame["horizon_periods"] = [(t - anchor) / PERIOD for t in targets]

    # Intensity lags, measured back from the ISSUE TIME. Each is a single
    # number broadcast across every horizon: at issue time there is one "value
    # 24 hours ago", not one per target.
    for hours in INTENSITY_LAG_HOURS:
        stamp = anchor - timedelta(hours=hours)
        candidates = series.loc[series.index <= stamp]
        frame[f"intensity_lag_{hours}h"] = float(candidates.iloc[-1]) if len(candidates) else np.nan

    for hours in ROLLING_WINDOWS_HOURS:
        window = series.loc[series.index > anchor - timedelta(hours=hours)]
        frame[f"intensity_mean_{hours}h"] = float(window.mean()) if len(window) else np.nan
        frame[f"intensity_std_{hours}h"] = float(window.std()) if len(window) > 1 else np.nan

    # Generation mix at the last observable period.
    if not mix.empty:
        observable_mix = mix.loc[mix.index <= anchor]
        if not observable_mix.empty:
            last = observable_mix.iloc[-1]
            for column in ("wind_perc", "solar_perc", "low_carbon_perc"):
                frame[f"mix_{column}_at_issue"] = float(last[column])
            recent = observable_mix.loc[observable_mix.index > anchor - timedelta(hours=24)]
            frame["mix_wind_mean_24h"] = (
                float(recent["wind_perc"].mean()) if len(recent) else np.nan
            )

    # Weather FOR THE TARGET, from the forecast vintage. This is the one group
    # legitimately drawn from the future: a weather forecast for tomorrow is
    # information a live system genuinely holds today.
    if not weather.empty:
        aligned = weather.reindex(targets)
        for column in aligned.columns:
            frame[column] = aligned[column].astype(float).to_numpy()

        # The ramp signal: how much windier the target hour is than the last
        # day has been. Level matters less than change — the grid responds to
        # wind arriving, not to wind being present.
        wind_columns = [c for c in weather.columns if c.startswith("wind_speed_100m_kmh__")]
        if wind_columns:
            trailing = weather.loc[weather.index <= anchor].tail(PERIODS_PER_DAY)
            if not trailing.empty:
                for column in wind_columns:
                    frame[f"ramp_{column}"] = frame[column] - float(trailing[column].mean())

    return frame


def assert_no_banned_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Fail if a feature frame contains something it must never contain.

    A second line of defence behind the dbt lineage test. That test asserts the
    warehouse models do not depend on the archive; this asserts the frame handed
    to a model does not carry a banned column under any name.
    """
    banned = {"index", "intensity_index", "index_band", "actual_gco2_kwh", "eso_forecast_gco2_kwh"}
    present = banned & set(frame.columns)
    if present:
        raise LeakageError(
            f"feature frame contains banned column(s): {sorted(present)}. "
            "The target and the ESO forecast are not features."
        )
    return frame
