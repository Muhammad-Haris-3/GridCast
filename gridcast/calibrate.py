"""Compute and store the interval calibration (M9, NFR-13).

    python -m gridcast.calibrate            # compute and store a new set
    python -m gridcast.calibrate --show     # print the set currently in force

Published intervals come from the model's own error distribution: for each
horizon band, the spread of the seasonal-naive error over a year of matured
actuals. Sixteen numbers, calibrated on 17,520 observations.

Issuing used to derive them from scratch on every run. The window is a
modelling choice and a good one — a year of samples is far past where a
quantile estimate stops moving, and the archive's older years describe a grid
with substantially more coal in it. But sixteen numbers fitted on a year do not
change perceptibly in thirty minutes, and recomputing them 48 times a day meant
shipping a year of history out of the database to arrive at the previous
answer. On a plan metered in bytes read it was the single largest recurring
cost in the project, and it helped exhaust the allowance on 2026-08-17.

So the window is unchanged and the frequency is not. This runs once a day.
Issuing reads sixteen rows.

There is exactly one implementation of the calibration itself — this module
calls :func:`gridcast.forecast.empirical_error_quantiles`, the same function
that produced the numbers before. What is stored is its output, not a
reimplementation of it.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime, timedelta

import psycopg

from gridcast.config import get_settings
from gridcast.db import connect
from gridcast.usage import record_on_exit, should_decline

# Past this, the calibration in force is old enough to say so. It is not an
# error: intervals fitted on a year are still very nearly right after a few
# days, and refusing to forecast over a stale calibration would turn a missed
# daily job into an outage. Loud, not fatal.
STALE_AFTER = timedelta(days=3)


def compute_and_store() -> tuple[uuid.UUID, int]:
    """Derive the calibration from history and append it. Returns (run id, rows)."""
    # Imported here rather than at module scope: gridcast.forecast pulls in
    # pandas and numpy, and --show has no need of either.
    from gridcast.forecast import (
        ERROR_BANDS,
        ERROR_HISTORY_DAYS,
        empirical_error_quantiles,
        error_band_lag,
        load_actuals,
    )

    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=ERROR_HISTORY_DAYS) if ERROR_HISTORY_DAYS else None

    actual = load_actuals(since)
    if actual.empty:
        raise RuntimeError("no matured actuals available; cannot calibrate")

    quantiles = empirical_error_quantiles(actual)
    if not quantiles:
        raise RuntimeError(
            f"only {len(actual):,} matured periods available, which is below the "
            "minimum the calibration needs. Expected while the archive is young."
        )

    run_id = uuid.uuid4()
    rows = 0

    with connect() as conn, conn.cursor() as cur:
        for (low, high), offsets in quantiles.items():
            # The count the estimate actually rests on, band by band. A long
            # band steps back further and therefore has fewer differences to
            # measure, which is a real difference between the bands and not a
            # detail worth flattening into one number.
            n_samples = len(actual) - error_band_lag(high)
            for name, offset in offsets.items():
                cur.execute(
                    """
                    INSERT INTO register.reg_error_quantile
                        (calibration_run_id, band_low, band_high, quantile_name,
                         offset_gco2_kwh, n_samples, source_days, computed_by_commit)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        low,
                        high,
                        name,
                        offset,
                        n_samples,
                        ERROR_HISTORY_DAYS,
                        settings.build_id,
                    ),
                )
                rows += cur.rowcount

    expected = len(ERROR_BANDS) * len(quantiles[ERROR_BANDS[0]])
    if rows != expected:
        raise RuntimeError(f"stored {rows} calibration rows, expected {expected}")

    return run_id, rows


def load_calibration() -> tuple[dict[tuple[int, int], dict[str, float]], datetime | None]:
    """The newest complete calibration, and when it was computed.

    Returns the same shape :func:`empirical_error_quantiles` returns, so the
    caller cannot tell which produced it — which is the point. An empty dict
    means nothing has been stored yet, and the caller decides what to do about
    that rather than being handed a silent default.

    Selecting on `calibration_run_id` rather than a timestamp range is what
    makes a whole set arrive or none of it. Rows from two calibrations mixed
    together would produce an interval that was never computed by anything.

    A missing table reads as "nothing stored", not as an error. Schema is
    applied to production by hand (README, "Running it locally"), so this code
    will be deployed before sql/007 is, and the honest behaviour in that window
    is the behaviour that existed before this module: fall back and compute.
    Crashing the pipeline over a table that has not arrived yet would make an
    optimisation into an outage.
    """
    try:
        with connect(readonly=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT band_low, band_high, quantile_name, offset_gco2_kwh, computed_at_utc
                  FROM register.reg_error_quantile
                 WHERE calibration_run_id = (
                           SELECT calibration_run_id
                             FROM register.reg_error_quantile
                            ORDER BY computed_at_utc DESC, calibration_id DESC
                            LIMIT 1
                       )
                """
            )
            rows = cur.fetchall()
    except psycopg.errors.UndefinedTable:
        print("register.reg_error_quantile does not exist yet — apply sql/007")
        return {}, None

    if not rows:
        return {}, None

    quantiles: dict[tuple[int, int], dict[str, float]] = {}
    for row in rows:
        band = (row["band_low"], row["band_high"])
        quantiles.setdefault(band, {})[row["quantile_name"]] = float(row["offset_gco2_kwh"])

    return quantiles, rows[0]["computed_at_utc"]


def main() -> int:
    # Registered before any work, so a run that dies mid-read still
    # accounts for what it spent (NFR-13).
    record_on_exit("calibrate")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show", action="store_true", help="Print the calibration in force without computing one"
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"database: {settings.database_url.split('@')[-1].split('?')[0] or 'NOT CONFIGURED'}")

    if args.show:
        quantiles, computed_at = load_calibration()
        if not quantiles:
            print("no calibration stored yet")
            return 1
        age = datetime.now(UTC) - computed_at
        print(f"computed {computed_at:%Y-%m-%d %H:%M}Z ({age.total_seconds() / 3600:.1f} h ago)")
        for (low, high), offsets in sorted(quantiles.items()):
            spread = "  ".join(f"{name} {value:+8.2f}" for name, value in sorted(offsets.items()))
            print(f"  h{low:>3}-{high:<3} {spread}")
        return 0

    # The one job in the pipeline that should stand down when the allowance is
    # nearly gone. It is the largest single read in the project — a year of
    # actuals — and its output is the only one that genuinely tolerates being a
    # day old: issuing keeps the previous calibration and says how stale it is.
    #
    # Issuing and scoring deliberately do NOT consult this. A forecast not
    # written is evidence permanently absent from the register, and spending
    # the last of an allowance on it is the right trade.
    if should_decline("calibrate"):
        print("calibration skipped; the set in force stays in force")
        return 0

    run_id, rows = compute_and_store()
    print(f"stored calibration {run_id} — {rows} offsets")

    quantiles, _ = load_calibration()
    for (low, high), offsets in sorted(quantiles.items()):
        spread = "  ".join(f"{name} {value:+8.2f}" for name, value in sorted(offsets.items()))
        print(f"  h{low:>3}-{high:<3} {spread}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
