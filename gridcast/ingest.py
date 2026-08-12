"""Ingestion: fetch a window from a source and land it.

    python -m gridcast.ingest --source ci_intensity --days 2
    python -m gridcast.ingest --source ci_intensity --from 2018-05-09 --to 2019-01-01
    python -m gridcast.ingest --scheduled
    python -m gridcast.ingest --daily

Backfill is not a separate code path. It is this, called with a wider window —
which is why a backfill that dies halfway can simply be run again rather than
reasoned about.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from gridcast.db import connect
from gridcast.landing import write_records
from gridcast.runlog import RunContext
from gridcast.sources import DAILY, REGISTRY, SCHEDULED, SourceSpec

# These are free, public, unfunded services. A pause between requests is not
# politeness for its own sake — an impolite client gets the project blocked, and
# there is no support queue to appeal to.
PAUSE_SECONDS = 1.0

# Rows per INSERT. Large enough that a 28-day window is a couple of statements,
# small enough that one statement's parameter list stays manageable.
BATCH_ROWS = 500


def windows(
    window_from: datetime, window_to: datetime, span: timedelta
) -> Iterator[tuple[datetime, datetime]]:
    """Split a range into chunks the upstream API will accept."""
    cursor = window_from
    while cursor < window_to:
        end = min(cursor + span, window_to)
        yield cursor, end
        cursor = end


def _batched(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def ingest_source(
    spec: SourceSpec,
    window_from: datetime,
    window_to: datetime,
    *,
    run_id: uuid.UUID,
    pause: float = PAUSE_SECONDS,
) -> tuple[int, int]:
    """Ingest one source over one range. Returns (rows_read, rows_written)."""
    with RunContext(
        run_id,
        source=spec.name,
        job="ingest",
        window_from=window_from,
        window_to=window_to,
    ) as run:
        for index, (chunk_from, chunk_to) in enumerate(
            windows(window_from, window_to, spec.max_window)
        ):
            if index:
                time.sleep(pause)

            records = list(spec.fetch(chunk_from, chunk_to))
            run.http_calls += 1
            run.rows_read += len(records)

            if not records:
                # An empty window is not automatically wrong — the range may
                # predate the source, or sit in the future. It is left to gap
                # detection to decide, because only the spine knows which
                # periods were supposed to exist.
                continue

            with connect() as conn:
                for batch in _batched(records, BATCH_ROWS):
                    run.rows_written += write_records(conn, spec, batch, run_id=run_id)

            print(
                f"  {spec.name}: {chunk_from:%Y-%m-%d} to {chunk_to:%Y-%m-%d} "
                f"read {len(records):>6,} written {run.rows_written:>6,}",
                flush=True,
            )

        return run.rows_read, run.rows_written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", choices=sorted(REGISTRY), help="Ingest one named source")
    group.add_argument("--scheduled", action="store_true", help=f"Ingest {', '.join(SCHEDULED)}")
    group.add_argument("--daily", action="store_true", help=f"Ingest {', '.join(DAILY)}")

    parser.add_argument("--from", dest="date_from", help="Start date, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="End date, YYYY-MM-DD (exclusive)")
    parser.add_argument(
        "--days",
        type=float,
        default=2.0,
        help="Trailing window in days when --from is not given. Default 2, which "
        "comfortably re-covers anything a missed run left behind.",
    )
    parser.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    args = parser.parse_args()

    now = datetime.now(UTC)
    if args.date_from:
        window_from = datetime.fromisoformat(args.date_from).replace(tzinfo=UTC)
        window_to = (
            datetime.fromisoformat(args.date_to).replace(tzinfo=UTC)
            if args.date_to
            else now + timedelta(days=2)
        )
    else:
        # The forward edge extends past now so that forecast-bearing sources —
        # the ESO forecast and the weather forecast — are collected for periods
        # that have not happened yet. Stopping at now would silently ingest only
        # the past and leave every forward horizon empty.
        window_from = now - timedelta(days=args.days)
        window_to = now + timedelta(days=2)

    if args.source:
        names = [args.source]
    elif args.scheduled:
        names = list(SCHEDULED)
    else:
        names = list(DAILY)

    run_id = uuid.uuid4()
    print(f"run {run_id} | {window_from:%Y-%m-%d %H:%M} to {window_to:%Y-%m-%d %H:%M} UTC")

    failures: list[str] = []
    for name in names:
        spec = REGISTRY[name]
        try:
            read, written = ingest_source(
                spec, window_from, window_to, run_id=run_id, pause=args.pause
            )
            print(f"{name}: read {read:,}, written {written:,}")
        except Exception as exc:  # noqa: BLE001
            # One source failing must not stop the others (R-3). The run log
            # already holds the detail; the exit code carries the verdict.
            print(f"{name}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
            failures.append(name)

    if failures:
        print(f"\n{len(failures)} source(s) failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
