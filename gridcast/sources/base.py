"""What every source has in common.

A source knows three things: how to fetch a window of its own data, what the
natural key of one record is, and how wide a window the upstream API will
tolerate. Everything else — hashing, insert-if-changed, run logging, chunking,
gap detection — is shared machinery that does not care which API it is talking
to.

Keeping it that way is what makes a new source a small file rather than a
project.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


def canonical_hash(payload: dict[str, Any]) -> bytes:
    """A stable sha256 over a record.

    Keys are sorted and separators fixed so the same content always hashes the
    same way. This is deliberately computed on our canonical form rather than on
    what Postgres stores: jsonb reorders keys and drops duplicates, so hashing
    the stored value would make the digest depend on the database's internals.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class Record:
    """One row destined for a landing table."""

    key: dict[str, Any]
    payload: dict[str, Any]

    @property
    def payload_hash(self) -> bytes:
        return canonical_hash(self.payload)


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Everything the ingestion machinery needs to know about one source."""

    name: str
    landing_table: str

    # (column, postgres type) in key order. The types are explicit because a
    # VALUES list without casts is inferred as text, and comparing a text
    # timestamp against a timestamptz column silently matches nothing — which
    # would make insert-if-changed insert every row, every run, forever.
    key_columns: Sequence[tuple[str, str]]

    # The key column used to bound range lookups. Must appear in key_columns.
    time_column: str

    # Largest window the upstream API accepts. Measured, not taken from docs:
    # the Carbon Intensity docs say 14 days and the API allows 30; Elexon
    # allows 28. Both reject a larger window with an explicit HTTP 400.
    max_window: timedelta

    fetch: Callable[[datetime, datetime], Iterator[Record]]

    # Sources that describe settlement periods can be checked against the spine
    # for gaps. Weather is hourly and keyed by location, so it is excluded.
    gap_checkable: bool = True

    # Deferred sources are not ingested and not gap-healed.
    #
    # This is a storage decision with a measured basis. On the first live
    # pipeline run, ci_regional and ex_price accounted for 13,240 of 13,888 rows
    # written — 95% — while the four sources M5 actually needs accounted for
    # 648. Regional rows are 823 bytes against an intensity row's 300, because
    # each carries a full generation-mix array per region, and regional can
    # never be scored at all: the API publishes a forecast and no actual.
    #
    # Spending the storage budget on data for a milestone three away, at the
    # cost of the weather history M6 needs now, is the wrong trade. Both are
    # re-fetchable from the API when the planner reaches them.
    #
    # Deferring rather than deleting the source keeps the decision reversible
    # and visible: the code that reads these sources still exists and still
    # works, and turning them back on is one flag.
    deferred: bool = False

    @property
    def key_names(self) -> list[str]:
        return [name for name, _ in self.key_columns]
