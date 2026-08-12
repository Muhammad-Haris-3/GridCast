"""Open-Meteo weather.

Free and keyless for non-commercial use. Three endpoints that mean three
different things, and conflating any two of them would create leakage:

* **archive** — reanalysis actuals. What the weather *was*. Legitimate for
  descriptive analysis and never a training feature, because a model in
  production will never have it.
* **forecast** — what is predicted now, for the next 48 hours. What the live
  system genuinely has at issue time.
* **vintage** (historical-forecast) — what was predicted at a past moment, as
  issued. The only honest way to train on history: it reproduces the quality of
  information a forecast would actually have had.

Training on archive actuals would teach a model to rely on perfect knowledge of
future weather. It would backtest beautifully and fail in production, and the
gap would be invisible until the live scoreboard opened.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta

from gridcast.http import get_json
from gridcast.sources.base import Record, SourceSpec

ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"
VINTAGE_BASE = "https://historical-forecast-api.open-meteo.com/v1/forecast"

HOURLY_VARIABLES = "temperature_2m,wind_speed_100m,shortwave_radiation,cloud_cover"

# wind_speed_100m rather than 10m: turbine hub height is the physically relevant
# level for generation, and the two differ substantially in stable conditions.

# ---------------------------------------------------------------------------
# Sample locations (design doc D-3, provisional)
#
# Six points chosen to span the three things that move GB carbon intensity:
# where the wind blows, where the sun shines, and where demand sits. They are
# provisional — M2 measures each one's correlation with national wind share and
# demand, and drops or reweights accordingly. They are committed here so the
# backfill has something to run against, not because the set is settled.
# ---------------------------------------------------------------------------
LOCATIONS: list[tuple[str, float, float]] = [
    ("scotland_north", 57.5, -4.0),  # onshore wind and hydro
    ("scotland_south", 55.8, -3.5),  # onshore wind, Central Belt demand
    ("north_sea", 54.5, 1.0),  # offshore wind: Dogger Bank, Hornsea
    ("irish_sea", 53.7, -3.6),  # offshore wind: Walney, Burbo Bank
    ("midlands", 52.5, -1.5),  # demand centre
    ("south_coast", 50.9, -1.4),  # solar, and southern demand
]

# D-3 RESOLVED at M4, by the rule fixed in audit/E01 before the numbers were
# seen: keep a location whose correlation with national wind share exceeds 0.5.
#
#   irish_sea       0.726        midlands        0.715
#   north_sea       0.716        scotland_south  0.652
#   south_coast     0.589        scotland_north  0.460  <- fails
#
# No pair of locations correlated above 0.85 with each other, so the redundancy
# rule never fired and only the floor did.
#
# scotland_north is the least useful despite being the northernmost, and also
# the least windy of the six at 22.9 km/h mean. 57.5N -4.0 is inland Highlands:
# Open-Meteo models the mountain interior there, while GB onshore wind capacity
# sits on coasts and ridgelines. The point measures the wrong Scotland.
#
# It stays in LOCATIONS and keeps being ingested — the series is the evidence
# for this decision and costs little — but it is excluded from the feature set.
EXCLUDED_FROM_FEATURES: frozenset[str] = frozenset({"scotland_north"})

FEATURE_LOCATIONS: list[str] = [
    name for name, _, _ in LOCATIONS if name not in EXCLUDED_FROM_FEATURES
]

LATITUDES = ",".join(str(lat) for _, lat, _ in LOCATIONS)
LONGITUDES = ",".join(str(lon) for _, _, lon in LOCATIONS)

ARCHIVE_MAX_WINDOW = timedelta(days=90)
VINTAGE_MAX_WINDOW = timedelta(days=30)


def _records_from_response(response: object) -> Iterator[Record]:
    """Turn Open-Meteo's column-oriented response into one record per hour.

    A multi-coordinate request returns a list, one entry per location in the
    order requested; a single-coordinate request returns a bare object. Both
    shapes are handled so the caller never has to care.
    """
    blocks: Sequence[dict] = response if isinstance(response, list) else [response]  # type: ignore[assignment]

    for index, block in enumerate(blocks):
        if index >= len(LOCATIONS):
            break
        location_id = LOCATIONS[index][0]
        hourly = block.get("hourly") or {}
        times = hourly.get("time") or []
        variables = [name for name in hourly if name != "time"]

        for position, stamp in enumerate(times):
            values = {name: hourly[name][position] for name in variables}
            if all(value is None for value in values.values()):
                # Open-Meteo pads the tail of an archive window with nulls when
                # reanalysis has not caught up. Storing those would create rows
                # that look like observations and contain nothing.
                continue
            yield Record(
                key={
                    "location_id": location_id,
                    "hour_start_utc": datetime.fromisoformat(stamp + "+00:00"),
                },
                payload={"time": stamp, "location_id": location_id, **values},
            )


def _params(window_from: datetime, window_to: datetime) -> dict[str, str]:
    return {
        "latitude": LATITUDES,
        "longitude": LONGITUDES,
        "hourly": HOURLY_VARIABLES,
        "timezone": "UTC",
        "start_date": window_from.date().isoformat(),
        "end_date": window_to.date().isoformat(),
    }


def _clamp_to_past(window_to: datetime) -> datetime:
    """Backward-looking endpoints cannot be asked about the future.

    Ingestion windows deliberately run two days past now, so that sources
    carrying forecasts collect periods which have not happened yet. The archive
    and vintage endpoints describe only what already occurred and reject such a
    range outright, so their end is clamped here rather than the shared window
    being narrowed — narrowing it would silently stop collecting every forward
    horizon the project exists to measure.
    """
    return min(window_to, datetime.now(window_to.tzinfo))


def fetch_archive(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """Reanalysis actuals. Descriptive use only — never a training feature."""
    window_to = _clamp_to_past(window_to)
    if window_from.date() > window_to.date():
        return
    yield from _records_from_response(
        get_json(ARCHIVE_BASE, params=_params(window_from, window_to))
    )


def fetch_forecast(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """The forward forecast the live system actually has.

    The window arguments are ignored: this endpoint returns whatever it
    currently predicts, and asking it for a past range would silently answer
    with something else. Three days covers the 48-hour horizon with margin.
    """
    response = get_json(
        FORECAST_BASE,
        params={
            "latitude": LATITUDES,
            "longitude": LONGITUDES,
            "hourly": HOURLY_VARIABLES,
            "timezone": "UTC",
            "forecast_days": "3",
        },
    )
    yield from _records_from_response(response)


def fetch_vintage(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """Past forecasts as they were issued. The leakage-safe training source."""
    window_to = _clamp_to_past(window_to)
    if window_from.date() > window_to.date():
        return
    yield from _records_from_response(
        get_json(VINTAGE_BASE, params=_params(window_from, window_to))
    )


ARCHIVE = SourceSpec(
    name="om_archive",
    landing_table="landing.lnd_om_archive",
    key_columns=[("location_id", "text"), ("hour_start_utc", "timestamptz")],
    time_column="hour_start_utc",
    max_window=ARCHIVE_MAX_WINDOW,
    fetch=fetch_archive,
    gap_checkable=False,  # hourly and per-location; the spine is half-hourly national
)

FORECAST = SourceSpec(
    name="om_forecast",
    landing_table="landing.lnd_om_forecast",
    key_columns=[("location_id", "text"), ("hour_start_utc", "timestamptz")],
    time_column="hour_start_utc",
    max_window=timedelta(days=3),
    fetch=fetch_forecast,
    gap_checkable=False,
)

VINTAGE = SourceSpec(
    name="om_vintage",
    landing_table="landing.lnd_om_vintage",
    key_columns=[("location_id", "text"), ("hour_start_utc", "timestamptz")],
    time_column="hour_start_utc",
    max_window=VINTAGE_MAX_WINDOW,
    fetch=fetch_vintage,
    gap_checkable=False,
)
