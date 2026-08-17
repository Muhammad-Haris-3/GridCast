"""The issuing read windows, tested rather than asserted.

Issuing was reading a year of actuals and thirty days of weather every thirty
minutes, on a database whose free tier meters bytes read. Narrowing those reads
is only safe if the rows removed were rows nothing consulted, and "I read the
function and it looked fine" is not evidence — the whole point of the reduction
is that the discarded rows are invisible in the output either way.

So the claim is tested directly: build features from the wide frame and from
the narrow one and require the results to be identical. A window that has been
cut too far shows up here as a changed feature, not as a quiet NaN six weeks
later.

No database. These are properties of the feature code, and a fixture series is
a better witness than live data — it can contain the gap that matters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from gridcast.baselines import PERIOD, SEASONAL_WALKBACK_STEPS
from gridcast.features import (
    FEATURE_REACH_HOURS,
    PERIODS_PER_DAY,
    SERVING_HISTORY_DAYS,
    WEATHER_TRAILING_DAYS,
    build_features,
)
from gridcast.forecast import (
    ERROR_BANDS,
    ISSUING_HISTORY_DAYS,
    QUANTILE_LEVELS,
    error_band_lag,
)

ANCHOR = datetime(2026, 6, 1, tzinfo=UTC)
HORIZONS = 96
LOCATIONS = ("midlands", "north_sea")


def periods(start: datetime, count: int) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([start + i * PERIOD for i in range(count)])


def ordinal(index: pd.DatetimeIndex) -> list[float]:
    """Periods since a fixed epoch — a value that names its own timestamp.

    Keyed to the timestamp rather than to position in the frame. Numbering from
    the start of each frame instead would give the wide and narrow reads
    different values for the same period, and every comparison below would fail
    on the fixture rather than on the thing being tested. That is not
    hypothetical: it is what the first version of this file did.
    """
    return [float((stamp - ANCHOR) / PERIOD) for stamp in index]


def intensity_frame(days_back: int) -> pd.DataFrame:
    """Actuals ending at the anchor, valued so a lookup names the period it found."""
    count = days_back * PERIODS_PER_DAY
    index = periods(ANCHOR - count * PERIOD, count + 1)
    frame = pd.DataFrame(
        {
            "actual_gco2_kwh": ordinal(index),
            "knowable_at_utc": index,
            "knowable_is_reconstructed": False,
        },
        index=index,
    )
    frame["knowable_effective_utc"] = frame["knowable_at_utc"]
    return frame


def mix_frame(days_back: int) -> pd.DataFrame:
    count = days_back * PERIODS_PER_DAY
    index = periods(ANCHOR - count * PERIOD, count + 1)
    values = ordinal(index)
    return pd.DataFrame(
        {
            "wind_perc": [v % 40 for v in values],
            "solar_perc": [v % 17 for v in values],
            "low_carbon_perc": [v % 63 for v in values],
            "knowable_at_utc": index,
        },
        index=index,
    )


def weather_frame(since: datetime, until: datetime) -> pd.DataFrame:
    """Wide weather, exactly as load_weather_history returns it after the pivot."""
    index = pd.DatetimeIndex(pd.date_range(since, until, freq="30min", tz="UTC"))
    values = ordinal(index)
    data = {}
    for location in LOCATIONS:
        data[f"wind_speed_100m_kmh__{location}"] = [v % 55 for v in values]
        data[f"temperature_2m_c__{location}"] = [v % 23 for v in values]
        data[f"shortwave_radiation_wm2__{location}"] = [v % 800 for v in values]
    return pd.DataFrame(data, index=index)


def features_from(weather: pd.DataFrame, intensity: pd.DataFrame, mix: pd.DataFrame):
    targets = periods(ANCHOR + PERIOD, HORIZONS)
    return build_features(
        ANCHOR,
        targets,
        intensity=intensity,
        mix=mix,
        weather=weather,
        anchor=ANCHOR,
    )


# ---------------------------------------------------------------------------
# The weather window
# ---------------------------------------------------------------------------


def test_narrow_weather_produces_identical_features():
    """Weather between the trailing window and the targets is provably dead.

    build_features consults weather at the target periods and across the 48
    periods at or before the anchor. Nothing reads the span in between, so
    removing it from the read must not move a single feature.
    """
    last_target = ANCHOR + HORIZONS * PERIOD
    intensity = intensity_frame(SERVING_HISTORY_DAYS)
    mix = mix_frame(SERVING_HISTORY_DAYS)

    wide = weather_frame(ANCHOR - timedelta(days=30), last_target)
    narrow = weather_frame(ANCHOR - timedelta(days=WEATHER_TRAILING_DAYS), last_target)

    assert len(narrow) < len(wide) / 5, "the narrow read should be a fraction of the wide one"

    pd.testing.assert_frame_equal(
        features_from(wide, intensity, mix),
        features_from(narrow, intensity, mix),
    )


def test_weather_trailing_window_covers_the_ramp():
    """The ramp averages the 48 periods before the anchor; the window must hold them."""
    assert WEATHER_TRAILING_DAYS * PERIODS_PER_DAY >= PERIODS_PER_DAY


def test_weather_narrowing_survives_a_gap_before_the_anchor():
    """A gap inside the trailing window must not change the ramp against the wide read.

    The ramp takes the last 48 available rows, not the last 24 hours, so under a
    gap it reaches further back than a day. That is the case the trailing window
    is sized for, and it is the one that would silently diverge if it were not.
    """
    last_target = ANCHOR + HORIZONS * PERIOD
    intensity = intensity_frame(SERVING_HISTORY_DAYS)
    mix = mix_frame(SERVING_HISTORY_DAYS)

    gap_from = ANCHOR - timedelta(hours=18)
    gap_to = ANCHOR - timedelta(hours=6)

    def drop_gap(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.loc[~((frame.index > gap_from) & (frame.index < gap_to))]

    wide = drop_gap(weather_frame(ANCHOR - timedelta(days=30), last_target))
    narrow = drop_gap(weather_frame(ANCHOR - timedelta(days=WEATHER_TRAILING_DAYS), last_target))

    pd.testing.assert_frame_equal(
        features_from(wide, intensity, mix),
        features_from(narrow, intensity, mix),
    )


# ---------------------------------------------------------------------------
# The intensity and mix window
# ---------------------------------------------------------------------------


def test_serving_window_exceeds_the_feature_reach():
    """The window must clear the furthest lag, with slack for a gap across it.

    Equality would be a bug: the 168-hour lag takes the last row at or before
    its stamp, so a window ending exactly on the stamp leaves the feature NaN
    the moment a period is missing there.
    """
    assert SERVING_HISTORY_DAYS * 24 > FEATURE_REACH_HOURS
    assert SERVING_HISTORY_DAYS * 24 - FEATURE_REACH_HOURS >= 24 * 7


def test_narrow_intensity_window_produces_identical_features():
    """Trimming history beyond the reach must not move a feature.

    The wide frame carries thirty days, the narrow one the derived window.
    Every feature reaches at most FEATURE_REACH_HOURS back, so the two must
    agree exactly.
    """
    last_target = ANCHOR + HORIZONS * PERIOD
    weather = weather_frame(ANCHOR - timedelta(days=WEATHER_TRAILING_DAYS), last_target)

    wide = features_from(weather, intensity_frame(30), mix_frame(30))
    narrow = features_from(
        weather, intensity_frame(SERVING_HISTORY_DAYS), mix_frame(SERVING_HISTORY_DAYS)
    )

    # Positional values make this strict: the lag features name the period they
    # came from, so an off-by-one window shows up as a changed number rather
    # than a plausible one.
    for column in ("intensity_lag_168h", "intensity_mean_168h", "intensity_std_24h"):
        assert column in wide.columns
    pd.testing.assert_frame_equal(wide, narrow)


# ---------------------------------------------------------------------------
# The issuing window for the baselines
# ---------------------------------------------------------------------------


def test_issuing_window_covers_the_seasonal_walkback():
    """Nothing older than the walk-back can reach a forecast, so the window bounds it.

    Derived from SEASONAL_WALKBACK_STEPS rather than typed, so lengthening the
    walk-back cannot leave the loader fetching too little — which would show up
    as a NaN point forecast at the far horizons and nowhere else.
    """
    assert ISSUING_HISTORY_DAYS > SEASONAL_WALKBACK_STEPS
    assert ISSUING_HISTORY_DAYS - SEASONAL_WALKBACK_STEPS >= 7


# ---------------------------------------------------------------------------
# The calibration the issuing window no longer computes
# ---------------------------------------------------------------------------


def test_calibration_set_is_the_size_the_writer_checks():
    """gridcast.calibrate refuses to store a set that is not this size."""
    assert len(ERROR_BANDS) * len(QUANTILE_LEVELS) == 16


@pytest.mark.parametrize(("low", "high"), ERROR_BANDS)
def test_error_band_lag_is_whole_days_and_covers_the_band(low: int, high: int):
    """The lag steps in whole days and reaches at least as far as the band's top.

    A lag shorter than the horizon it calibrates would measure a one-day error
    and publish it as the uncertainty of a two-day forecast.
    """
    lag = error_band_lag(high)
    assert lag % PERIODS_PER_DAY == 0
    assert lag >= high or high < PERIODS_PER_DAY
    assert low <= high
