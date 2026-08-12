"""Gap detection and self-healing (SRS FR-4).

GitHub's cron is best-effort. Runs are delayed under load, skipped, and
disabled outright after sixty days of repository inactivity. None of that is
worked around, because none of it can be: the schedule is somebody else's
promise and it is not a strong one.

What is under our control is making lateness harmless. Every pipeline run ends
by asking the spine which settlement periods should exist, comparing that to
what landed, and refetching whatever is missing. A run that never happened
becomes a gap that the next run closes.

This is why the target in NFR-1 is coverage, not punctuality. Chasing
punctuality on a free scheduler would be chasing something we cannot have.

    python -m gridcast.gapfill --lookback 14
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

from psycopg import sql

from gridcast.db import connect
from gridcast.ingest import ingest_source
from gridcast.runlog import RunContext
from gridcast.sources import REGISTRY, SourceSpec

# How far behind now to stop looking. A period that ended twenty minutes ago has
# not necessarily been published yet, and treating it as missing would have the
# pipeline hammering the API for a row that does not exist. This is a
# publication allowance, not a fudge factor.
GRACE = timedelta(hours=3)


def find_gaps(spec: SourceSpec, *, lookback: timedelta, grace: timedelta = GRACE) -> list[datetime]:
    """Settlement periods the spine expects but landing does not have.

    Absence is the question, not emptiness. A period whose row exists with a
    null actual is *pending*, not missing, and must not be refetched forever —
    which is exactly the distinction a spine makes possible and a bare
    ``SELECT`` over the landing table cannot.
    """
    schema, table = spec.landing_table.split(".")
    now = datetime.now(UTC)

    statement = sql.SQL("""
        SELECT d.sp_start_utc
          FROM marts.dim_settlement_period d
         WHERE d.sp_start_utc >= %s
           AND d.sp_start_utc <  %s
           AND NOT EXISTS (
               SELECT 1 FROM {schema}.{table} l
                WHERE l.sp_start_utc = d.sp_start_utc
               )
           -- Periods verified absent at source are not gaps and must not be
           -- refetched. M2 finding A02 confirmed 179 periods across five
           -- windows never existed upstream; without this exclusion the daily
           -- deep-heal re-requests all five every night, for ever, against a
           -- free public API with no terminating condition. That would be an
           -- impolite loop, and it would be GridCast's fault rather than the
           -- ESO's.
           AND NOT EXISTS (
               SELECT 1 FROM marts.mart_absent_periods a
                WHERE a.sp_start_utc = d.sp_start_utc
                  AND a.source = %s
               )
         ORDER BY d.sp_start_utc
    """).format(schema=sql.Identifier(schema), table=sql.Identifier(table))

    with connect() as conn, conn.cursor() as cur:
        cur.execute(statement, (now - lookback, now - grace, spec.name))
        return [row["sp_start_utc"] for row in cur.fetchall()]


def coalesce(periods: Sequence[datetime]) -> Iterator[tuple[datetime, datetime]]:
    """Group consecutive half-hourly periods into contiguous windows.

    Six hundred missing periods are one outage, not six hundred incidents.
    Refetching them one at a time would be six hundred requests against a free
    API to recover what two will.
    """
    if not periods:
        return

    step = timedelta(minutes=30)
    start = previous = periods[0]

    for period in periods[1:]:
        if period - previous > step:
            yield start, previous + step
            start = period
        previous = period

    yield start, previous + step


def heal(spec: SourceSpec, *, lookback: timedelta, run_id: uuid.UUID) -> tuple[int, int]:
    """Find gaps for one source and refetch them. Returns (periods, windows)."""
    gaps = find_gaps(spec, lookback=lookback)
    if not gaps:
        return 0, 0

    ranges = list(coalesce(gaps))
    with RunContext(
        run_id,
        source=spec.name,
        job="gapfill",
        window_from=gaps[0],
        window_to=gaps[-1],
    ) as run:
        # rows_read is the size of the hole, rows_written is what filling it
        # actually recovered. The refetches log their own rows too, but a parent
        # that reported zero written would read as a failed heal in the very log
        # someone consults after an outage.
        run.rows_read = len(gaps)
        for window_from, window_to in ranges:
            _, written = ingest_source(spec, window_from, window_to, run_id=run_id)
            run.rows_written += written

        # A heal that recovered fewer periods than were missing is a partial
        # result, not a success. Usually it means the upstream never published
        # them, which is worth seeing rather than smoothing over.
        run.partial = run.rows_written < len(gaps)
        healed = run.rows_written

    return len(gaps), healed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback", type=int, default=14, help="Days back to inspect")
    parser.add_argument("--source", choices=sorted(REGISTRY), help="Limit to one source")
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="Report gaps without refetching",
    )
    args = parser.parse_args()

    # Say which database this is talking to. .env points at a local
    # scratch database by default, and a command that silently answers
    # from the wrong one looks exactly like an answer — which is how the
    # M2 audit reported findings from 239 local rows instead of 144,763.
    from gridcast.config import get_settings as _s

    print(f"database: {_s().database_url.split('@')[-1].split('?')[0] or 'NOT CONFIGURED'}")
    lookback = timedelta(days=args.lookback)
    names = [args.source] if args.source else list(REGISTRY)
    run_id = uuid.uuid4()
    total = 0

    for name in names:
        spec = REGISTRY[name]
        if not spec.gap_checkable or spec.deferred:
            continue

        if args.detect_only:
            gaps = find_gaps(spec, lookback=lookback)
            windows = list(coalesce(gaps))
            total += len(gaps)
            state = (
                f"{len(gaps):,} missing period(s) in {len(windows)} window(s)"
                if gaps
                else "no gaps"
            )
            print(f"  {spec.name:<14} {state}")
            for window_from, window_to in windows[:5]:
                print(f"      {window_from:%Y-%m-%d %H:%M} -> {window_to:%Y-%m-%d %H:%M}")
            continue

        missing, healed = heal(spec, lookback=lookback, run_id=run_id)
        total += missing
        if not missing:
            print(f"  {spec.name:<14} no gaps")
        elif healed >= missing:
            print(f"  {spec.name:<14} healed {healed:,} of {missing:,} period(s)")
        else:
            print(
                f"  {spec.name:<14} PARTIAL: recovered {healed:,} of {missing:,} period(s) "
                f"— the remainder were never published upstream"
            )

    verb = "detected" if args.detect_only else "found and refetched"
    print(f"\n{total:,} missing period(s) {verb} across {len(names)} source(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
