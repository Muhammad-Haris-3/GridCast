"""Elexon BMRS Insights.

No authentication. Supplies the two things the Carbon Intensity API cannot:
demand in absolute MW, and price.

The reason this source matters disproportionately is `publishTime`. Every
record states when it was published, separately from the settlement period it
describes. That is what makes a point-in-time-correct feature possible rather
than merely intended: without it, "what did we believe demand was yesterday at
14:00" would have to be guessed from a revision that has since been superseded.

Window limit measured 2026-08-12: 28 days inclusive, rejected above that with an
explicit HTTP 400 naming the limit.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

from gridcast.http import get_json
from gridcast.sources.base import Record, SourceSpec

BASE = "https://data.elexon.co.uk/bmrs/api/v1"

# 21 days against a measured 28-day ceiling. The limit is stated inclusive of
# both endpoints, so a naive 28-day span is already at the boundary.
MAX_WINDOW = timedelta(days=21)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_demand(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """Initial national and transmission-system demand outturn, in MW.

    Keyed on (period, publish time) so that revisions accumulate rather than
    replace. The latest value is what a dashboard wants; the value as published
    at a past instant is what a forecast feature needs, and only one of those
    can be recovered after the fact.
    """
    response = get_json(
        f"{BASE}/demand/outturn",
        params={
            "settlementDateFrom": window_from.date().isoformat(),
            "settlementDateTo": window_to.date().isoformat(),
            "format": "json",
        },
    )
    for element in response.get("data", []):
        yield Record(
            key={
                "sp_start_utc": _instant(element["startTime"]),
                "publish_time_utc": _instant(element["publishTime"]),
            },
            payload=element,
        )


def fetch_price(window_from: datetime, window_to: datetime) -> Iterator[Record]:
    """Market index price per settlement period, per data provider.

    Provider stays in the key rather than being averaged away. The two
    providers do not agree: N2EXMIDP frequently reports zero price on zero
    volume while APXMIDP reports a real trade. Averaging them at ingestion
    would bake that artefact into every downstream figure with no way back.
    """
    response = get_json(
        f"{BASE}/balancing/pricing/market-index",
        params={
            "from": window_from.strftime("%Y-%m-%dT%H:%MZ"),
            "to": window_to.strftime("%Y-%m-%dT%H:%MZ"),
            "format": "json",
        },
    )
    for element in response.get("data", []):
        yield Record(
            key={
                "sp_start_utc": _instant(element["startTime"]),
                "data_provider": element["dataProvider"],
            },
            payload=element,
        )


DEMAND = SourceSpec(
    name="ex_demand",
    landing_table="landing.lnd_ex_demand",
    key_columns=[("sp_start_utc", "timestamptz"), ("publish_time_utc", "timestamptz")],
    time_column="sp_start_utc",
    max_window=MAX_WINDOW,
    fetch=fetch_demand,
)

PRICE = SourceSpec(
    name="ex_price",
    landing_table="landing.lnd_ex_price",
    key_columns=[("sp_start_utc", "timestamptz"), ("data_provider", "text")],
    time_column="sp_start_utc",
    max_window=MAX_WINDOW,
    fetch=fetch_price,
)
