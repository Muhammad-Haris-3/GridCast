"""Database transfer accounting (NFR-13).

    python -m gridcast.usage --report     # month-to-date against the budget
    python -m gridcast.usage --check      # non-zero when over the ceiling

Neon's free tier meters **bytes read out of the database**. On 2026-08-17 that
allowance ran out and the project stopped — API, pages and pipeline together,
because one allowance feeds all three. Nothing had been watching it. There was
no counter, no trend, and no warning; the first signal was a connection
refusal, by which point the register had already stopped growing.

This module is the counter that should have existed. Every row fetched through
gridcast.db is measured and attributed to the job that asked for it, and each
run appends its total. What that buys is not primarily the ceiling — it is the
trend. A query that quietly starts reading ten times more is invisible until
something is adding it up.

ON THE NUMBER BEING AN ESTIMATE.

This measures the width of the values actually returned, not the bytes on the
wire. It cannot see protocol framing, TLS overhead, or compression, and it will
disagree with Neon's own figure — by a margin, and in a direction that varies
with the query.

It is reported as an estimate everywhere it is shown, and it is deliberately
NOT calibrated against the console figure to look more accurate than it is. Its
job is to make a tenfold regression obvious on the day it lands, and a
consistent estimate does that as well as an exact one. Treating it as the
authority on how much allowance remains is the one use it does not support.
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from gridcast.config import get_settings

# Neon's free-tier monthly data transfer allowance. A published figure about
# somebody else's product, so it is stated in one place and read from here
# rather than being spread across comments that will not be updated together.
FREE_TIER_BUDGET_BYTES = 5 * 1024**3

# Where warnings start and where automated work should start declining to run.
# Two thresholds because they are two different decisions: the first asks a
# human to look, the second stops the pipeline spending what is left on work
# that can wait a day.
WARN_FRACTION = 0.75
DECLINE_FRACTION = 0.90

# Per-row protocol overhead: a DataRow message header plus a length prefix per
# field. Close enough to the real framing to keep the estimate honest at the
# small-row end, where a naive sum of value widths understates badly.
ROW_OVERHEAD_BYTES = 7
FIELD_OVERHEAD_BYTES = 4


def value_width(value: Any) -> int:
    """Estimated wire width of one value, from the value itself.

    Measured rather than assumed wherever the type has a variable width: a text
    column holding a 2 KB JSON blob and one holding a status word are not the
    same read, and a fixed per-column cost would make the two indistinguishable
    in exactly the case worth catching.
    """
    if value is None:
        return FIELD_OVERHEAD_BYTES
    if isinstance(value, bool):
        return FIELD_OVERHEAD_BYTES + 1
    if isinstance(value, int):
        return FIELD_OVERHEAD_BYTES + 8
    if isinstance(value, float):
        return FIELD_OVERHEAD_BYTES + 8
    if isinstance(value, bytes | bytearray | memoryview):
        return FIELD_OVERHEAD_BYTES + len(value)
    if isinstance(value, str):
        return FIELD_OVERHEAD_BYTES + len(value.encode("utf-8"))
    if isinstance(value, datetime | date | time):
        return FIELD_OVERHEAD_BYTES + 8
    if isinstance(value, Decimal):
        return FIELD_OVERHEAD_BYTES + len(str(value))
    if isinstance(value, uuid.UUID):
        return FIELD_OVERHEAD_BYTES + 16
    if isinstance(value, list | tuple | dict):
        return FIELD_OVERHEAD_BYTES + len(str(value).encode("utf-8"))
    return FIELD_OVERHEAD_BYTES + len(str(value).encode("utf-8"))


class Meter:
    """Running total for one process.

    Process-scoped rather than global-with-reset: every job in this project is
    a short-lived `python -m` invocation, so the process boundary and the unit
    of work are the same thing. A meter that had to be reset by hand would
    eventually be read after somebody forgot to.
    """

    def __init__(self) -> None:
        self.queries = 0
        self.rows = 0
        self.bytes_estimated = 0

    def record(self, rows: Sequence[Any] | Iterable[Any]) -> None:
        materialised = list(rows)
        self.queries += 1
        self.rows += len(materialised)
        for row in materialised:
            values = row.values() if isinstance(row, dict) else row
            self.bytes_estimated += ROW_OVERHEAD_BYTES + sum(value_width(v) for v in values)

    def summary(self) -> str:
        return (
            f"{self.rows:,} rows over {self.queries:,} quer"
            f"{'y' if self.queries == 1 else 'ies'}, ~{human_bytes(self.bytes_estimated)}"
        )


METER = Meter()


def human_bytes(count: int | float) -> str:
    step = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if step < 1024 or unit == "GB":
            return f"{step:,.1f} {unit}" if unit != "B" else f"{step:,.0f} B"
        step /= 1024
    return f"{step:,.1f} GB"


def period_start(now: datetime | None = None) -> datetime:
    """The instant the current billing period began.

    Neon resets the transfer allowance on the billing period, which is not
    necessarily the first of the month. GRIDCAST_BILLING_PERIOD_DAY carries the
    day it actually resets, so the figure on the status page is measured over
    the window that will actually be reset rather than over a calendar month
    that happens to be convenient.
    """
    now = now or datetime.now(UTC)
    day = max(1, min(28, get_settings().billing_period_day))

    if now.day >= day:
        return now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)

    month = now.month - 1 or 12
    year = now.year - 1 if month == 12 else now.year
    return now.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)


def record_run(job: str, run_id: uuid.UUID | None = None) -> None:
    """Append this process's total. Never raises.

    A failure here must not fail the job that was being measured. Accounting
    that can take down the thing it accounts for is worse than no accounting,
    and the specific case is unmissable: the table lives in the database whose
    exhaustion this exists to predict, so it will be unreachable at exactly the
    moment it is most interesting.
    """
    from gridcast.db import connect  # local: gridcast.db imports this module

    if METER.queries == 0:
        return

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO landing.db_transfer
                    (run_id, job, queries, rows_returned, bytes_estimated, code_commit)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    job,
                    METER.queries,
                    METER.rows,
                    METER.bytes_estimated,
                    get_settings().build_id,
                ),
            )
    except Exception as exc:  # noqa: BLE001 — accounting must not break the job
        print(f"could not record transfer usage: {type(exc).__name__}: {exc}")


def record_on_exit(job: str, run_id: uuid.UUID | None = None) -> None:
    """Record this process's transfer when it ends, however it ends.

    Registered at the top of a job rather than called at the bottom, because
    the runs worth measuring include the ones that raise. A job that died
    halfway through a large read spent that transfer, and an accounting that
    only records clean exits would show the allowance draining into nothing.
    """
    import atexit

    atexit.register(record_run, job, run_id)


def period_total() -> tuple[int, int, int]:
    """(bytes, rows, runs) recorded since the billing period began."""
    from gridcast.db import connect

    with connect(readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT coalesce(sum(bytes_estimated), 0) AS bytes_estimated,
                   coalesce(sum(rows_returned), 0)   AS rows_returned,
                   count(*)                          AS runs
              FROM landing.db_transfer
             WHERE recorded_at_utc >= %s
            """,
            (period_start(),),
        )
        row = cur.fetchone()

    if not row:
        return 0, 0, 0
    return int(row["bytes_estimated"]), int(row["rows_returned"]), int(row["runs"])


def budget_status() -> dict[str, Any]:
    """Month-to-date usage against the allowance, for the status surface."""
    used, rows, runs = period_total()
    budget = get_settings().transfer_budget_bytes or FREE_TIER_BUDGET_BYTES
    fraction = used / budget if budget else 0.0

    return {
        "period_start_utc": period_start().isoformat(),
        "bytes_estimated": used,
        "bytes_estimated_human": human_bytes(used),
        "budget_bytes": budget,
        "budget_human": human_bytes(budget),
        "fraction_used": round(fraction, 4),
        "rows_returned": rows,
        "runs_recorded": runs,
        "state": "over"
        if fraction >= DECLINE_FRACTION
        else "warn"
        if fraction >= WARN_FRACTION
        else "ok",
        "estimate_note": (
            "Measured from the width of returned values, not from the wire. It will "
            "disagree with the provider's figure; it exists to make a regression "
            "visible, not to say how much allowance is left."
        ),
    }


def should_decline(job: str) -> bool:
    """Whether deferrable work should stand down this run.

    Only ever consulted by jobs whose output survives being a day old. Issuing
    and scoring never call this: a forecast not written is evidence permanently
    missing from the register, and the register is the project. Spending the
    last of an allowance on it is the correct trade.
    """
    try:
        status = budget_status()
    except Exception as exc:  # noqa: BLE001 — an unreadable budget is not a reason to stop
        print(f"could not read the transfer budget ({type(exc).__name__}); proceeding")
        return False

    if status["state"] != "over":
        return False

    print(
        f"::warning title=Transfer budget::{job} is standing down. "
        f"Estimated {status['bytes_estimated_human']} of {status['budget_human']} used "
        f"since {status['period_start_utc'][:10]} "
        f"({status['fraction_used']:.0%}). This job's output tolerates being a day old; "
        "issuing and scoring do not and are unaffected."
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Print month-to-date usage")
    parser.add_argument(
        "--check", action="store_true", help="Exit non-zero when past the decline threshold"
    )
    args = parser.parse_args()

    status = budget_status()
    print(f"billing period began {status['period_start_utc'][:10]}")
    print(
        f"estimated transfer   {status['bytes_estimated_human']} of "
        f"{status['budget_human']} ({status['fraction_used']:.1%})"
    )
    print(f"rows returned        {status['rows_returned']:,} over {status['runs_recorded']:,} runs")
    print(f"note                 {status['estimate_note']}")

    if status["state"] == "over":
        print(
            f"::error title=Transfer budget::Past {DECLINE_FRACTION:.0%} of the "
            "allowance. Deferrable jobs will stand down; issuing and scoring continue."
        )
        return 1 if args.check else 0

    if status["state"] == "warn":
        print(
            f"::warning title=Transfer budget::Past {WARN_FRACTION:.0%} of the allowance "
            "with the period still open."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
