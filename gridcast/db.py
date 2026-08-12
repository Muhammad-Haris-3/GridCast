"""Database access.

Thin helpers over psycopg3. No ORM: every query in this project is written to
be readable as SQL, because the SQL is part of the deliverable.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from gridcast.config import get_settings


@contextmanager
def connect(url: str | None = None, *, readonly: bool = False) -> Iterator[psycopg.Connection]:
    """Open a connection. Commits on clean exit, rolls back on exception."""
    settings = get_settings()
    dsn = url or (settings.serving_url if readonly else settings.database_url)
    if not dsn:
        raise RuntimeError(
            "No database URL configured. Set GRIDCAST_DATABASE_URL "
            "(and GRIDCAST_READONLY_DATABASE_URL for the API)."
        )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if readonly:
            conn.read_only = True

        # Every connection speaks UTC, regardless of where it runs.
        #
        # Without this, a developer in Karachi and a container in Frankfurt
        # return different offsets for the same instant, and — worse —
        # date_trunc() over a timestamptz silently uses the session timezone.
        # The register is sealed by UTC month (sql/003_register.sql), so a
        # connection with a different timezone would partition the seal
        # differently and produce a mismatching hash on otherwise identical
        # data. Pinning it here makes that impossible rather than unlikely.
        conn.execute("SET TIME ZONE 'UTC'")

        yield conn


def fetch_all(
    sql: str, params: Sequence[Any] | dict[str, Any] | None = None, *, readonly: bool = False
) -> list[dict[str, Any]]:
    with connect(readonly=readonly) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(
    sql: str, params: Sequence[Any] | dict[str, Any] | None = None, *, readonly: bool = False
) -> dict[str, Any] | None:
    with connect(readonly=readonly) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount
