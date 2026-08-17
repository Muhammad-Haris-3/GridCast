"""The transfer counter (NFR-13).

The allowance that stopped this project on 2026-08-17 was metered in bytes
read, and nothing was counting them. This is the counter that should have
existed, so it is worth testing the two things it actually has to get right:

  * it counts rows that CROSS THE WIRE, not rows scanned — otherwise a
    server-side aggregate looks as expensive as the table it aggregates, and
    the one fix that works looks like it does not;
  * it never takes down the job it is measuring.

The byte figure itself is an estimate and is not tested for accuracy, because
it does not have any. What is tested is that it moves with the size of what was
returned, which is the property the trend depends on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gridcast.usage import (
    DECLINE_FRACTION,
    FIELD_OVERHEAD_BYTES,
    ROW_OVERHEAD_BYTES,
    WARN_FRACTION,
    Meter,
    human_bytes,
    period_start,
    value_width,
)

# ---------------------------------------------------------------------------
# What the meter counts
# ---------------------------------------------------------------------------


def test_a_wide_read_costs_more_than_a_narrow_one():
    """The estimate has to move with what was returned, or the trend is noise."""
    narrow, wide = Meter(), Meter()
    narrow.record([{"n": i} for i in range(100)])
    wide.record([{"n": i, "label": "x" * 200} for i in range(100)])

    assert wide.bytes_estimated > narrow.bytes_estimated * 5


def test_an_aggregate_costs_one_row_however_much_it_scanned():
    """The whole argument for pushing work into SQL, asserted.

    score.py computes its MASE scale as a server-side avg() over two years of
    actuals and pays for one row. If the meter charged for rows scanned, that
    query would look like the most expensive in the project and the pressure
    would run the wrong way.
    """
    aggregate, materialised = Meter(), Meter()
    aggregate.record([{"avg": 21.4}])
    materialised.record([{"value": float(i)} for i in range(35_040)])

    assert aggregate.bytes_estimated < materialised.bytes_estimated / 1000


def test_an_empty_result_still_counts_as_a_query():
    """A query returning nothing did still make a round trip."""
    meter = Meter()
    meter.record([])
    assert meter.queries == 1
    assert meter.rows == 0


def test_rows_accumulate_across_queries():
    meter = Meter()
    meter.record([{"a": 1}, {"a": 2}])
    meter.record([{"a": 3}])
    assert (meter.queries, meter.rows) == (2, 3)


# ---------------------------------------------------------------------------
# Width estimation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, True, 42, 3.5, "text", b"bytes", Decimal("1.25"), uuid.uuid4(), datetime.now(UTC)],
)
def test_every_supported_type_has_a_positive_width(value):
    assert value_width(value) >= FIELD_OVERHEAD_BYTES


def test_a_long_string_costs_more_than_a_short_one():
    """Variable-width columns are measured, not assigned a flat cost.

    A jsonb payload column and a status word are not the same read, and the
    landing tables hold both.
    """
    assert value_width("x" * 1000) > value_width("x") + 900


def test_an_unknown_type_is_still_counted():
    """An unmapped type must not silently cost nothing."""

    class Odd:
        def __str__(self) -> str:
            return "a moderately long representation"

    assert value_width(Odd()) > FIELD_OVERHEAD_BYTES


def test_row_overhead_is_charged_once_per_row():
    """Two one-field rows cost more than one two-field row of the same values."""
    split, together = Meter(), Meter()
    split.record([{"a": 1}, {"b": 2}])
    together.record([{"a": 1, "b": 2}])
    assert split.bytes_estimated - together.bytes_estimated == ROW_OVERHEAD_BYTES


# ---------------------------------------------------------------------------
# The billing period
# ---------------------------------------------------------------------------


def test_the_period_starts_on_the_configured_day(monkeypatch):
    """Measured over the window that actually resets, not the calendar month."""
    from gridcast import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("GRIDCAST_BILLING_PERIOD_DAY", "12")
    try:
        assert period_start(datetime(2026, 8, 20, tzinfo=UTC)) == datetime(2026, 8, 12, tzinfo=UTC)
        # Before the reset day, the open period began in the previous month.
        assert period_start(datetime(2026, 8, 3, tzinfo=UTC)) == datetime(2026, 7, 12, tzinfo=UTC)
        # And across a year boundary.
        assert period_start(datetime(2026, 1, 5, tzinfo=UTC)) == datetime(2025, 12, 12, tzinfo=UTC)
    finally:
        config.get_settings.cache_clear()


def test_thresholds_are_ordered():
    """Warning before standing down, or the warning never arrives."""
    assert 0 < WARN_FRACTION < DECLINE_FRACTION < 1


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def test_human_bytes_scales():
    assert human_bytes(512).endswith("B")
    assert "KB" in human_bytes(2048)
    assert "MB" in human_bytes(5 * 1024**2)
    assert "GB" in human_bytes(3 * 1024**3)


def test_human_bytes_does_not_run_out_of_units():
    """A wildly oversized figure must still render, not raise."""
    assert "GB" in human_bytes(9 * 1024**5)
