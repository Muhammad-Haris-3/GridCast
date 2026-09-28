"""One failing source costs only the models that depend on it.

On 2026-09-28 at 00:30Z om_forecast timed out, and because "Issue forecasts"
only ran when every step before it had succeeded, nothing was issued at all —
not B0, not B1, not ESO_published, none of which reads weather. The pipeline
now issues after an ingest failure, which makes the other half of the bargain
load-bearing: G2, the one model that does read weather, must refuse to issue on
weather that does not cover its targets, and must say so in the run log rather
than write rows that are recorded as G2 and are not.

These drive gridcast.forecast.main end to end with every database touch
replaced, so what is asserted is what the issuing job would have written.

No database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from gridcast import forecast
from gridcast.features import PERIOD, WeatherCoverageError, weather_columns

WEATHER_FREE = {"B1_seasonal_naive_q_v1", "B0_persistence_v1", "ESO_published"}


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


class FakeRun:
    """Stands in for RunContext, and keeps what would have gone to run_log."""

    log: list[FakeRun] = []

    def __init__(self, run_id, *, source, job, window_from=None, window_to=None) -> None:  # noqa: ARG002
        self.source = source
        self.job = job
        self.rows_read = 0
        self.rows_written = 0
        self.failure: BaseException | None = None

    def __enter__(self) -> FakeRun:
        FakeRun.log.append(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.failure = exc
        return False


class FakeModel:
    """Takes NaN without complaint, as HistGradientBoosting does. That is the
    whole problem: nothing downstream of the guard would notice."""

    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return np.full(len(matrix), self.value)


def fake_bundle() -> dict:
    wind = [c for c in weather_columns() if c.startswith("wind_speed_100m_kmh__")]
    return {
        "features": [*weather_columns(), *(f"ramp_{c}" for c in wind)],
        "model": FakeModel(150.0),
        "quantile_models": {
            "q025": FakeModel(100.0),
            "q10": FakeModel(120.0),
            "q90": FakeModel(180.0),
            "q975": FakeModel(200.0),
        },
        "conformal": {},
    }


def actual_series() -> pd.Series:
    end = pd.Timestamp.now(tz="UTC").floor("30min") - PERIOD
    index = pd.date_range(end - timedelta(days=8), end, freq="30min")
    return pd.Series(np.linspace(100.0, 200.0, len(index)), index=index)


def intensity_history(since: datetime) -> pd.DataFrame:
    actual = actual_series()
    actual = actual.loc[actual.index >= since]
    frame = pd.DataFrame(
        {
            "actual_gco2_kwh": actual.to_numpy(),
            "knowable_at_utc": actual.index,
            "knowable_is_reconstructed": False,
        },
        index=actual.index,
    )
    frame["knowable_effective_utc"] = frame["knowable_at_utc"]
    return frame


def weather_until(stop: datetime | None):
    """A live-forecast loader whose frame ends at `stop`, or holds nothing.

    `stop=None` means the forecast reaches as far as it was asked for.
    """

    def load(since: datetime, until: datetime) -> pd.DataFrame:
        last = until if stop is None else stop
        index = pd.date_range(since, last, freq="30min", tz="UTC")
        if stop is not None and stop < since:
            return pd.DataFrame()
        return pd.DataFrame(
            {column: np.linspace(1.0, 50.0, len(index)) for column in weather_columns()},
            index=index,
        )

    return load


@pytest.fixture
def issuing(monkeypatch: pytest.MonkeyPatch):
    """Run forecast.main with no database. Returns (written, run_log) per call."""
    written: dict[str, int] = {}
    FakeRun.log = []

    class Settings:
        database_url = ""
        build_id = "test"

    monkeypatch.setattr("sys.argv", ["gridcast.forecast"])
    monkeypatch.setattr(forecast, "record_on_exit", lambda job: None)
    monkeypatch.setattr(forecast, "get_settings", lambda: Settings())
    monkeypatch.setattr(forecast, "load_actuals", lambda since=None: actual_series())
    monkeypatch.setattr(
        forecast,
        "load_calibration",
        lambda: (
            {band: {"q10": -10.0, "q90": 10.0} for band in forecast.ERROR_BANDS},
            datetime.now(UTC),
        ),
    )
    monkeypatch.setattr(forecast, "ensure_models_registered", lambda commit: None)
    monkeypatch.setattr(
        forecast,
        "load_eso_forecast",
        lambda anchor: {anchor + h * PERIOD: 175.0 for h in range(1, forecast.HORIZONS + 1)},
    )
    monkeypatch.setattr(forecast, "load_g2", fake_bundle)
    monkeypatch.setattr(forecast, "RunContext", FakeRun)

    def write(version, rows, run_at, run_id):  # noqa: ARG001
        written[version] = len(rows)
        return len(rows)

    monkeypatch.setattr(forecast, "write_forecasts", write)
    monkeypatch.setattr("gridcast.features.load_intensity_history", intensity_history)
    monkeypatch.setattr("gridcast.features.load_mix_history", lambda since=None: pd.DataFrame())

    def run(weather_loader) -> tuple[dict[str, int], list[FakeRun]]:
        monkeypatch.setattr("gridcast.features.load_weather_forecast", weather_loader)
        assert forecast.main() == 0
        return written, FakeRun.log

    return run


def g2_log(log: list[FakeRun]) -> list[FakeRun]:
    return [run for run in log if run.source == "G2_gbm_v1"]


# ---------------------------------------------------------------------------
# The control: with good weather, everything issues
# ---------------------------------------------------------------------------


def test_with_weather_covering_every_target_all_four_models_issue(issuing) -> None:
    """Without this, the refusals below could be the harness refusing everything."""
    written, log = issuing(weather_until(None))

    assert set(written) == WEATHER_FREE | {"G2_gbm_v1"}
    assert all(count > 0 for count in written.values())
    assert [run.failure for run in g2_log(log)] == [None]


# ---------------------------------------------------------------------------
# (a) The weather ingest failed: the weather-free models issue anyway
# ---------------------------------------------------------------------------


def test_weather_free_models_issue_when_the_weather_ingest_failed(issuing) -> None:
    """The 2026-09-28 00:30Z run, as it should have gone.

    om_forecast failed and the live relation holds nothing. B0, B1 and the ESO
    benchmark read no weather, so they issue in full; G2 does not.
    """
    written, log = issuing(weather_until(datetime(2000, 1, 1, tzinfo=UTC)))

    assert set(written) == WEATHER_FREE
    assert all(count > 0 for count in written.values())

    for version in WEATHER_FREE:
        (run,) = [r for r in log if r.source == version]
        assert run.failure is None and run.rows_written == written[version]


# ---------------------------------------------------------------------------
# (b) Weather that does not reach the targets: G2 refuses, and says so
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "short_by",
    [
        pytest.param(timedelta(hours=1), id="tail-horizons-uncovered"),
        pytest.param(timedelta(hours=47), id="only-first-targets-covered"),
    ],
)
def test_g2_records_a_failure_and_issues_nothing_on_weather_short_of_the_targets(
    issuing, monkeypatch: pytest.MonkeyPatch, short_by: timedelta
) -> None:
    """A stale forecast that stops short of the furthest target.

    This is what a failed om_forecast leaves behind once the last good fetch no
    longer reaches past midnight. It used to be accepted, with the tail issued
    on NaN weather under G2's name.
    """

    def stale(since: datetime, until: datetime) -> pd.DataFrame:
        return weather_until(until - short_by)(since, until)

    written, log = issuing(stale)

    assert "G2_gbm_v1" not in written, "G2 issued on weather that did not reach its targets"
    assert set(written) == WEATHER_FREE

    (run,) = g2_log(log)
    assert isinstance(run.failure, WeatherCoverageError)
    assert "short of the furthest target" in str(run.failure)
    assert run.rows_written == 0


def test_g2_records_a_failure_when_the_live_forecast_is_empty(issuing) -> None:
    written, log = issuing(lambda since, until: pd.DataFrame())

    assert "G2_gbm_v1" not in written
    (run,) = g2_log(log)
    assert isinstance(run.failure, WeatherCoverageError)
    assert "no weather rows" in str(run.failure)


def test_g2_records_a_failure_when_one_location_is_blank_at_a_target(issuing) -> None:
    """Reaching the targets is not enough if a location is missing there."""
    column = weather_columns()[0]

    def holed(since: datetime, until: datetime) -> pd.DataFrame:
        frame = weather_until(None)(since, until)
        frame.loc[frame.index[-3], column] = np.nan
        return frame

    written, log = issuing(holed)

    assert "G2_gbm_v1" not in written
    (run,) = g2_log(log)
    assert isinstance(run.failure, WeatherCoverageError)
    assert column in str(run.failure)
