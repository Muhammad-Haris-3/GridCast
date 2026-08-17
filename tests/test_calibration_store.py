"""The interval calibration store, against a real database.

Issuing no longer derives its intervals; it reads them from
`register.reg_error_quantile`. Two properties carry that change, and neither is
visible from the calling code:

  * a read returns one whole calibration, never a blend of two;
  * what is read back is what was written, to the bit.

The second matters more than it sounds. These offsets are the published
uncertainty of every forecast the project issues. A rounding step or a column
type that quietly truncated them would widen or narrow every interval on the
site, and the scoreboard would keep reporting coverage against the wrong
nominal without anything looking broken.

Marked `db`. Run with:  pytest -m db   (requires GRIDCAST_DATABASE_URL)
"""

from __future__ import annotations

import os
import uuid

import pytest

from gridcast.calibrate import load_calibration
from gridcast.db import connect

pytestmark = pytest.mark.db

INSERT = """
INSERT INTO register.reg_error_quantile
    (calibration_run_id, band_low, band_high, quantile_name,
     offset_gco2_kwh, n_samples, source_days, computed_by_commit)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

# Deliberately awkward values. Round numbers would survive a truncation this is
# meant to catch.
OFFSETS = {"q025": -61.478931, "q10": -28.10225, "q90": 27.999417, "q975": 60.3336}


@pytest.fixture(autouse=True)
def _requires_database():
    if not os.environ.get("GRIDCAST_DATABASE_URL"):
        pytest.skip("GRIDCAST_DATABASE_URL not set")


def store_set(run_id: uuid.UUID, bands, offsets, *, commit: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        for low, high in bands:
            for name, value in offsets.items():
                cur.execute(INSERT, (run_id, low, high, name, value, 17_000, 365, commit))


def test_a_stored_calibration_reads_back_unchanged():
    """Every offset survives the round trip exactly."""
    bands = [(1, 6), (7, 24)]
    store_set(uuid.uuid4(), bands, OFFSETS, commit="roundtrip-probe")

    loaded, computed_at = load_calibration()

    assert computed_at is not None
    for band in bands:
        assert loaded[band] == pytest.approx(OFFSETS, abs=0.0)


def test_a_read_never_blends_two_calibrations():
    """The newest set is returned whole, with nothing of the previous one in it.

    Written as two sets that disagree on every value and differ in which bands
    they cover. Selecting by timestamp range rather than by run id would let the
    older band survive into the result — an interval no calibration ever
    produced, assembled by the reader.
    """
    old_offsets = {name: value * 3 for name, value in OFFSETS.items()}
    store_set(uuid.uuid4(), [(1, 6), (7, 24), (25, 48)], old_offsets, commit="older-probe")
    store_set(uuid.uuid4(), [(1, 6), (7, 24)], OFFSETS, commit="newer-probe")

    loaded, _ = load_calibration()

    assert set(loaded) == {(1, 6), (7, 24)}, "a band from the older set leaked through"
    for band in loaded:
        assert loaded[band] == pytest.approx(OFFSETS, abs=0.0)


def test_a_calibration_cannot_be_edited_after_the_fact():
    """The offsets are append-only, like the forecasts they attach uncertainty to.

    Without this the intervals could be widened after an outcome fell outside
    them, which is the same failure the forecast register exists to prevent —
    one indirection further out.
    """
    run_id = uuid.uuid4()
    store_set(run_id, [(49, 96)], OFFSETS, commit="immutability-probe")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('gridcast_app', "
            "'register.reg_error_quantile', %s) AS granted",
            ("UPDATE",),
        )
        assert cur.fetchone()["granted"] is False, "gridcast_app can UPDATE the calibration"

        cur.execute(
            "SELECT has_table_privilege('gridcast_app', "
            "'register.reg_error_quantile', %s) AS granted",
            ("DELETE",),
        )
        assert cur.fetchone()["granted"] is False, "gridcast_app can DELETE the calibration"


def test_one_offset_per_band_and_quantile_within_a_run():
    """A retried insert cannot double a set and leave the reader to choose."""
    run_id = uuid.uuid4()
    store_set(run_id, [(1, 6)], OFFSETS, commit="unique-probe")

    with pytest.raises(Exception, match="error_quantile_unique|duplicate key"):
        store_set(run_id, [(1, 6)], OFFSETS, commit="unique-probe")
