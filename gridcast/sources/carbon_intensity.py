"""National Grid ESO Carbon Intensity API.

No authentication, CC BY 4.0. The primary source: it is the only one of the
three that publishes both a forecast and the realised actual for the same
settlement period, which is what makes scoring possible at all.

Window limits are measured, not taken from the documentation. The docs state a
14-day maximum; the API accepts 30 and rejects 31 with an explicit HTTP 400
naming the limit. Verified 2026-08-12.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

from gridcast.http import get_json
from gridcast.sources.base import Record, SourceSpec

BASE = "https://api.carbonintensity.org.uk"

# 28 days, not the 30 the API allows. The margin costs one extra request per
# fourteen months of backfill and removes any dependence on an undocumented
# boundary holding exactly where it does today.
MAX_WINDOW = timedelta(days=28)

# Regional responses carry all 17 DNO regions per settlement period, so a window
# of the same length returns roughly seventeen times the payload. Smaller
# windows keep individual responses to a sane size.
REGIONAL_MAX_WINDOW = timedelta(days=7)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%MZ")


def _period_start(element: dict) -> datetime:
    return datetime.fromisoformat(element["from"].replace("Z", "+00:00"))


def fetch_intensity(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """National carbon intensity: forecast, actual and index per period."""
    response = get_json(f"{BASE}/intensity/{_stamp(window_from)}/{_stamp(window_to)}")
    for element in response.get("data", []):
        yield Record(key={"sp_start_utc": _period_start(element)}, payload=element)


def fetch_genmix(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """National generation mix: percentage share for nine fuel types.

    Stored one row per settlement period with the fuel array intact, rather than
    nine rows. The array is unnested in staging, where a fuel category appearing
    or disappearing upstream is visible as a test failure rather than as a
    silently different row count here.
    """
    response = get_json(f"{BASE}/generation/{_stamp(window_from)}/{_stamp(window_to)}")
    for element in response.get("data", []):
        yield Record(key={"sp_start_utc": _period_start(element)}, payload=element)


def fetch_regional(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """Regional intensity for the 17 DNO regions.

    These records carry a forecast and **no actual**. Regional intensity can
    therefore never be scored, and nothing downstream may present it as
    validated (SRS 6.4, NFR-9, R-6). It is ingested because it informs where a
    load-shifting recommendation applies, not because it can be measured.
    """
    response = get_json(f"{BASE}/regional/intensity/{_stamp(window_from)}/{_stamp(window_to)}")
    for element in response.get("data", []):
        period_start = _period_start(element)
        for region in element.get("regions", []):
            yield Record(
                key={"sp_start_utc": period_start, "region_id": int(region["regionid"])},
                payload=region,
            )


INTENSITY = SourceSpec(
    name="ci_intensity",
    landing_table="landing.lnd_ci_intensity",
    key_columns=[("sp_start_utc", "timestamptz")],
    time_column="sp_start_utc",
    max_window=MAX_WINDOW,
    fetch=fetch_intensity,
)

GENMIX = SourceSpec(
    name="ci_genmix",
    landing_table="landing.lnd_ci_genmix",
    key_columns=[("sp_start_utc", "timestamptz")],
    time_column="sp_start_utc",
    max_window=MAX_WINDOW,
    fetch=fetch_genmix,
)

REGIONAL = SourceSpec(
    name="ci_regional",
    landing_table="landing.lnd_ci_regional",
    key_columns=[("sp_start_utc", "timestamptz"), ("region_id", "smallint")],
    time_column="sp_start_utc",
    max_window=REGIONAL_MAX_WINDOW,
    fetch=fetch_regional,
    # Deferred to M8. See SourceSpec.deferred for the measured basis.
    deferred=True,
)
