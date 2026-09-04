"""Which weather each caller reads, and what happens when it is not there.

The challenger stopped issuing on 2026-08-15 and it took three weeks for anyone
to find out. One crossed wire caused it: issuing read the VINTAGE weather
relation, which is backward-looking by construction and so holds no row for any
period being forecast.

It failed in both of the ways a crossed wire can. Quietly first — the frame
covered the recent past, every forward weather feature reindexed to NaN, and
HistGradientBoosting consumed them without complaint, so G2 issued forecasts
that were not G2. Then loudly, once the serving window narrowed to three days
and the vintage mart had stopped advancing: an empty frame, missing columns, a
KeyError, and no G2 forecasts at all.

The quiet failure is the one worth a test. A model that raises gets noticed. A
model that silently becomes a different model writes rows into an append-only
register that cannot be corrected, and they are scored as though they were real.

No database. These are properties of the code — which relation each loader
names, and what the feature builder does with weather that stops short.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from gridcast.features import (
    PERIOD,
    PERIODS_PER_DAY,
    WEATHER_LIVE_RELATION,
    WEATHER_TRAILING_DAYS,
    WEATHER_VINTAGE_RELATION,
    WeatherCoverageError,
    _load_weather,
    assert_weather_reaches,
    build_features,
    load_weather_forecast,
    load_weather_history,
)
from gridcast.sources import DAILY, SCHEDULED

ANCHOR = datetime(2026, 6, 1, tzinfo=UTC)
LOCATION = "midlands"


# ---------------------------------------------------------------------------
# A cursor that answers nothing and remembers what it was asked
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    def execute(self, sql: str, params: object = None) -> None:  # noqa: ARG002
        self.seen.append(sql)

    def fetchall(self) -> list[dict]:
        return []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeConnection:
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.seen)

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def queries(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the SQL a loader issues, without a database behind it."""
    seen: list[str] = []
    monkeypatch.setattr("gridcast.features.connect", lambda **_: FakeConnection(seen))
    return seen


def relation_of(sql: str) -> str:
    """The relation named in the FROM clause.

    Matched as a whole line rather than by substring, because
    `marts.fct_weather_period` is a prefix of `marts.fct_weather_period_live`.
    A substring test would find the vintage relation inside a live query and
    pass the exact assertion this file exists to make fail.
    """
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("FROM "):
            return stripped.removeprefix("FROM ").strip()
    raise AssertionError(f"no FROM clause in: {sql}")


# ---------------------------------------------------------------------------
# Which relation each caller reads
# ---------------------------------------------------------------------------


def test_training_reads_the_vintage_weather(queries: list[str]) -> None:
    """Training measures the past, so it needs what was believed at the time."""
    load_weather_history()

    assert len(queries) == 1
    assert relation_of(queries[0]) == WEATHER_VINTAGE_RELATION, (
        "Training must never read the live forecast. For a past hour it holds "
        "what was predicted shortly BEFORE that hour, so a 48-hour-out origin "
        "would train against weather it could not have held."
    )


def test_issuing_reads_the_live_forecast(queries: list[str]) -> None:
    """Issuing needs weather for periods that have not happened yet.

    Only the live forecast has them. This is the assertion whose absence cost
    the challenger three weeks of evidence.
    """
    load_weather_forecast(
        ANCHOR - timedelta(days=WEATHER_TRAILING_DAYS), ANCHOR + timedelta(days=2)
    )

    assert len(queries) == 1
    assert (
        relation_of(queries[0]) == WEATHER_LIVE_RELATION
    ), "Issuing must not read the vintage relation: it holds no future period."


def test_the_two_relations_are_not_the_same_table() -> None:
    assert WEATHER_VINTAGE_RELATION != WEATHER_LIVE_RELATION


def test_an_unrecognised_relation_is_refused() -> None:
    """The relation is interpolated into SQL, so it is checked rather than trusted."""
    with pytest.raises(ValueError, match="unknown weather relation"):
        _load_weather("marts.something_else", None, None)


# ---------------------------------------------------------------------------
# The silent failure, pinned
# ---------------------------------------------------------------------------


def intensity_frame() -> pd.DataFrame:
    index = pd.DatetimeIndex([ANCHOR - i * PERIOD for i in range(PERIODS_PER_DAY * 8, 0, -1)])
    frame = pd.DataFrame(
        {
            "actual_gco2_kwh": np.linspace(100.0, 200.0, len(index)),
            "knowable_at_utc": index,
            "knowable_is_reconstructed": False,
        },
        index=index,
    )
    frame["knowable_effective_utc"] = frame["knowable_at_utc"]
    return frame


def weather_frame(first: datetime, last: datetime) -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.date_range(first, last, freq="30min", tz="UTC"))
    return pd.DataFrame(
        {
            f"wind_speed_100m_kmh__{LOCATION}": np.linspace(10.0, 30.0, len(index)),
            f"temperature_2m_c__{LOCATION}": np.linspace(5.0, 15.0, len(index)),
            f"shortwave_radiation_wm2__{LOCATION}": np.linspace(0.0, 500.0, len(index)),
        },
        index=index,
    )


def targets(count: int = 96) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([ANCHOR + (i + 1) * PERIOD for i in range(count)])


def test_weather_that_stops_at_the_anchor_makes_every_target_feature_nan() -> None:
    """The failure the guard exists to catch, demonstrated rather than described.

    Nothing raises here. The frame is well formed, the columns are all present,
    and every one of them is NaN at every horizon — which is what reading the
    vintage relation at issue time produced for three days in August.
    """
    frame = build_features(
        ANCHOR,
        targets(),
        intensity=intensity_frame(),
        mix=pd.DataFrame(),
        weather=weather_frame(ANCHOR - timedelta(days=1), ANCHOR),
        anchor=ANCHOR,
    )

    column = f"wind_speed_100m_kmh__{LOCATION}"
    assert column in frame.columns, "the column exists, which is why nothing failed"
    assert frame[column].isna().all(), "and it is NaN for every horizon"
    assert frame[f"ramp_{column}"].isna().all()


def test_weather_covering_the_targets_produces_real_features() -> None:
    frame = build_features(
        ANCHOR,
        targets(),
        intensity=intensity_frame(),
        mix=pd.DataFrame(),
        weather=weather_frame(ANCHOR - timedelta(days=1), ANCHOR + timedelta(days=2)),
        anchor=ANCHOR,
    )

    column = f"wind_speed_100m_kmh__{LOCATION}"
    assert frame[column].notna().all()
    assert frame[f"ramp_{column}"].notna().all()


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_empty_weather_is_refused() -> None:
    with pytest.raises(WeatherCoverageError, match="no weather rows"):
        assert_weather_reaches(pd.DataFrame(), targets())


def test_weather_stopping_before_the_first_target_is_refused() -> None:
    stale = weather_frame(ANCHOR - timedelta(days=3), ANCHOR)
    with pytest.raises(WeatherCoverageError, match="before the first target"):
        assert_weather_reaches(stale, targets())


def test_weather_reaching_only_the_first_target_is_accepted() -> None:
    """Partial forward coverage is legitimate; no forward coverage is not.

    The upstream forecast is finite, and a run near its edge can outrun it. The
    tail horizons then carry NaN weather, which the model handles natively and
    the issuing job prints. Refusing that would mean withholding a whole
    forecast over the last few horizons of it.
    """
    short = weather_frame(ANCHOR - timedelta(days=1), ANCHOR + PERIOD)
    assert_weather_reaches(short, targets())


# ---------------------------------------------------------------------------
# The root cause
# ---------------------------------------------------------------------------


def test_the_vintage_weather_source_is_ingested_on_a_schedule() -> None:
    """fct_weather_hour can only advance if something keeps feeding it.

    The mart is incremental precisely so that lnd_om_vintage can be pruned
    daily. That arrangement needs both halves: om_vintage was in neither
    schedule, so the prune ran every night against a table nothing refilled,
    and the typed weather history stopped at whenever the last backfill ended —
    silently, because an incremental model that inserts nothing looks exactly
    like one with nothing to insert.
    """
    assert "om_vintage" in DAILY or "om_vintage" in SCHEDULED


def test_the_live_weather_source_is_ingested_every_run() -> None:
    """Issuing reads om_forecast's landing table at every run, half-hourly."""
    assert "om_forecast" in SCHEDULED
