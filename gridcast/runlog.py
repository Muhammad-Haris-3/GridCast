"""The run log (SRS FR-5).

Every ingestion, transformation and scoring job runs inside a :class:`RunContext`.
A job that fails still writes its row — the log is most useful precisely when
things go wrong, so recording only successes would be the wrong way round.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import TracebackType

from gridcast.db import connect


def new_run_id() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunContext:
    """Context manager that opens and closes one ``landing.run_log`` row.

    Usage::

        with RunContext(run_id, source="carbon_intensity", job="ingest") as run:
            run.http_calls += 1
            run.rows_read += len(records)
            run.rows_written += written
    """

    def __init__(
        self,
        run_id: uuid.UUID,
        *,
        source: str,
        job: str,
        window_from: datetime | None = None,
        window_to: datetime | None = None,
    ) -> None:
        self.run_id = run_id
        self.source = source
        self.job = job
        self.window_from = window_from
        self.window_to = window_to
        self.http_calls = 0
        self.rows_read = 0
        self.rows_written = 0
        self.partial = False
        # A failure the job caught itself, rather than one raised through the
        # block. See __exit__.
        self.failure: BaseException | None = None
        self._run_log_id: int | None = None

    def __enter__(self) -> RunContext:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO landing.run_log
                    (run_id, source, job, window_from_utc, window_to_utc, started_at_utc, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'running')
                RETURNING run_log_id
                """,
                (self.run_id, self.source, self.job, self.window_from, self.window_to, utcnow()),
            )
            row = cur.fetchone()
            self._run_log_id = row["run_log_id"] if row else None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # A caught failure counts. Some jobs swallow their own exception on
        # purpose — a challenger that cannot build its features must not stop
        # the champion being recorded, because the champion's series is the
        # evidence and the moment cannot be refilled later. Swallowing it into
        # a print, though, is how G2 stopped issuing on 2026-08-15 and nobody
        # noticed for three weeks: stdout belongs to the runner and is gone
        # within days, while landing.run_log is what the status page reads and
        # what anyone outside this process can see. Setting `failure` records
        # the row without re-raising through the block.
        caught = exc if exc_type is not None else self.failure

        if caught is None:
            status = "partial" if self.partial else "success"
            error_class = error_detail = None
        else:
            status = "failed"
            error_class = type(caught).__name__
            error_detail = str(caught)[:2000]

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE landing.run_log
                   SET finished_at_utc = %s,
                       http_calls      = %s,
                       rows_read       = %s,
                       rows_written    = %s,
                       status          = %s,
                       error_class     = %s,
                       error_detail    = %s
                 WHERE run_log_id = %s
                """,
                (
                    utcnow(),
                    self.http_calls,
                    self.rows_read,
                    self.rows_written,
                    status,
                    error_class,
                    error_detail,
                    self._run_log_id,
                ),
            )
        # Never suppress. A failed job must fail the workflow step, or a broken
        # pipeline looks healthy from the outside.
        return False
