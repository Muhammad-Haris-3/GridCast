"""Ingestion machinery: hashing, windowing, coalescing, and insert-if-changed.

The database-backed tests at the bottom are the ones that matter. Insert-if-
changed is the mechanism that delivers idempotency, revision history and the
knowability boundary all at once, so a subtle fault in it would corrupt three
requirements simultaneously and none of them loudly.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from gridcast.gapfill import coalesce
from gridcast.ingest import windows
from gridcast.landing import write_records
from gridcast.sources import REGISTRY
from gridcast.sources.base import Record, SourceSpec, canonical_hash

# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_hash_ignores_key_order() -> None:
    """jsonb reorders keys, so the digest must not depend on ordering.

    If it did, the same unchanged record could hash differently between runs
    and insert-if-changed would write a new row every time, forever.
    """
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_hash_notices_a_changed_value() -> None:
    assert canonical_hash({"actual": 200}) != canonical_hash({"actual": 201})


def test_hash_notices_null_becoming_a_number() -> None:
    """The single most important change this project needs to detect.

    A settlement period is first published with a null actual and later revised
    to carry one. Missing that transition would freeze every pending period as
    permanently unknown.
    """
    assert canonical_hash({"actual": None}) != canonical_hash({"actual": 137})


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------


def test_windows_never_exceed_the_api_limit() -> None:
    """Each upstream rejects an over-wide range with HTTP 400."""
    start = datetime(2018, 5, 9, tzinfo=UTC)
    end = datetime(2019, 5, 9, tzinfo=UTC)
    span = timedelta(days=28)

    chunks = list(windows(start, end, span))
    assert all((b - a) <= span for a, b in chunks)


def test_windows_are_contiguous_and_cover_the_whole_range() -> None:
    """A hole between chunks is a hole in the backfill nobody would notice."""
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 6, 1, tzinfo=UTC)
    chunks = list(windows(start, end, timedelta(days=28)))

    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for (_, previous_end), (next_start, _) in zip(chunks[:-1], chunks[1:], strict=True):
        assert previous_end == next_start


def test_windows_of_a_zero_length_range_is_empty() -> None:
    moment = datetime(2020, 1, 1, tzinfo=UTC)
    assert list(windows(moment, moment, timedelta(days=1))) == []


# ---------------------------------------------------------------------------
# Coalescing
# ---------------------------------------------------------------------------


def _periods(*offsets: int) -> list[datetime]:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    return [base + timedelta(minutes=30 * n) for n in offsets]


def test_coalesce_merges_a_contiguous_outage_into_one_window() -> None:
    """600 missing periods are one outage, not 600 incidents."""
    assert list(coalesce(_periods(0, 1, 2, 3))) == [
        (_periods(0)[0], _periods(4)[0]),
    ]


def test_coalesce_keeps_separate_outages_separate() -> None:
    result = list(coalesce(_periods(0, 1, 10, 11)))
    assert len(result) == 2
    assert result[0] == (_periods(0)[0], _periods(2)[0])
    assert result[1] == (_periods(10)[0], _periods(12)[0])


def test_coalesce_window_end_is_exclusive_of_the_next_period() -> None:
    """The window must cover the last missing period, not stop before it."""
    ((start, end),) = list(coalesce(_periods(5)))
    assert end - start == timedelta(minutes=30)


def test_coalesce_of_nothing_is_nothing() -> None:
    assert list(coalesce([])) == []


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_time_column_is_part_of_the_key(name: str) -> None:
    """The writer bounds its lookup by the time column.

    If it were not a key column the range filter would not use the index, and
    on a table of millions of rows the write path would degrade from an index
    scan to a sequential one on every single ingestion.
    """
    spec = REGISTRY[name]
    assert spec.time_column in spec.key_names


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_source_declares_a_positive_window(name: str) -> None:
    assert REGISTRY[name].max_window > timedelta(0)


# ---------------------------------------------------------------------------
# Insert-if-changed, against a real database
# ---------------------------------------------------------------------------

pytest_db = pytest.mark.skipif(
    not os.environ.get("GRIDCAST_DATABASE_URL"), reason="GRIDCAST_DATABASE_URL not set"
)

PROBE = SourceSpec(
    name="probe",
    landing_table="landing.lnd_ci_intensity",
    key_columns=[("sp_start_utc", "timestamptz")],
    time_column="sp_start_utc",
    max_window=timedelta(days=1),
    fetch=lambda a, b: iter(()),
)


@pytest.fixture
def clean_probe_window():
    """A far-future window, so the test cannot collide with real ingested data."""
    start = datetime(2031, 1, 1, tzinfo=UTC)
    url = os.environ["GRIDCAST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM landing.lnd_ci_intensity WHERE sp_start_utc >= %s", (start,))
    yield start
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM landing.lnd_ci_intensity WHERE sp_start_utc >= %s", (start,))


@pytest_db
def test_unchanged_payload_writes_nothing_on_reingest(clean_probe_window) -> None:
    """FR-3. Re-running any window must be a no-op."""
    start = clean_probe_window
    record = Record(key={"sp_start_utc": start}, payload={"intensity": {"actual": 100}})

    from gridcast.db import connect

    with connect() as conn:
        first = write_records(conn, PROBE, [record], run_id=uuid.uuid4())
    with connect() as conn:
        second = write_records(conn, PROBE, [record], run_id=uuid.uuid4())

    assert first == 1
    assert second == 0


@pytest_db
def test_a_revision_is_a_new_row_and_the_old_one_survives(clean_probe_window) -> None:
    """FR-9. History is preserved by the same mechanism that gives idempotency."""
    start = clean_probe_window
    from gridcast.db import connect

    pending = Record(key={"sp_start_utc": start}, payload={"intensity": {"actual": None}})
    revised = Record(key={"sp_start_utc": start}, payload={"intensity": {"actual": 137}})

    with connect() as conn:
        write_records(conn, PROBE, [pending], run_id=uuid.uuid4())
    with connect() as conn:
        written = write_records(conn, PROBE, [revised], run_id=uuid.uuid4())

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM landing.lnd_ci_intensity WHERE sp_start_utc = %s "
            "ORDER BY fetched_at_utc",
            (start,),
        )
        rows = cur.fetchall()

    assert written == 1
    assert len(rows) == 2, "the pending row must survive the revision"
    assert rows[0]["payload"]["intensity"]["actual"] is None
    assert rows[1]["payload"]["intensity"]["actual"] == 137


@pytest_db
def test_a_mixed_batch_writes_only_what_changed(clean_probe_window) -> None:
    """The realistic case: a window where most periods are unchanged."""
    start = clean_probe_window
    from gridcast.db import connect

    original = [
        Record(key={"sp_start_utc": start + timedelta(minutes=30 * n)}, payload={"v": n})
        for n in range(10)
    ]
    with connect() as conn:
        write_records(conn, PROBE, original, run_id=uuid.uuid4())

    mixed = list(original)
    mixed[3] = Record(key=original[3].key, payload={"v": 999})
    mixed[7] = Record(key=original[7].key, payload={"v": 888})

    with connect() as conn:
        written = write_records(conn, PROBE, mixed, run_id=uuid.uuid4())

    assert written == 2, "only the two changed periods should be written"
