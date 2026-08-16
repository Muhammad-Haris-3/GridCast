"""Database access.

Thin helpers over psycopg3. No ORM: every query in this project is written to
be readable as SQL, because the SQL is part of the deliverable.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from gridcast.config import get_settings


def ipv4_first(dsn: str) -> str:
    """Order the host's addresses so IPv4 is tried before IPv6.

    Neon publishes both A and AAAA records. psycopg 3.2 resolves the hostname
    itself, turns every address into a separate connection attempt, and tries
    them in the order the resolver returned them — so on a host with no IPv6
    route out, the attempts fail at the network layer before the credential is
    ever sent.

    That is not a hypothetical. The serving API on Render spent a day reporting
    the database as unreachable, with every attempt logging:

        connection to server at "2600:1f16:1c2b:...", port 5432 failed:
        Network is unreachable

    The database was fine and the URL was correct. The container simply has no
    IPv6, and free-tier hosting is not going to grow any.

    Resolution stays psycopg's job in spirit: this only reorders what the
    resolver returned, keeping every address as a fallback rather than pinning
    one. `host` is repeated alongside `hostaddr` so TLS still carries the
    hostname — Neon routes on SNI, and an address without a name reaches the
    proxy and is refused in a way that reads like a credential fault.

    Anything unexpected — no host, a literal address, a caller who already set
    hostaddr, a resolver failure, a host with only one address family — is left
    exactly as it came in, so this can only reorder attempts, never remove them.
    """
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return dsn

    host = params.get("host")
    if not isinstance(host, str) or not host or params.get("hostaddr"):
        return dsn
    if host.startswith("/") or "," in host:
        return dsn

    port = params.get("port") or 5432
    try:
        answers = socket.getaddrinfo(
            host, int(port), proto=socket.IPPROTO_TCP, type=socket.SOCK_STREAM
        )
    except (OSError, ValueError):
        # Let psycopg resolve and report the failure in its own words.
        return dsn

    # Only one family in play: nothing to reorder, so change nothing. An
    # IPv6-only host must still be tried over IPv6.
    families = {answer[0] for answer in answers}
    if socket.AF_INET not in families or socket.AF_INET6 not in families:
        return dsn

    ordered: list[str] = []
    for family in (socket.AF_INET, socket.AF_INET6):
        for answer in answers:
            address = answer[4][0]
            if answer[0] == family and address not in ordered:
                ordered.append(address)

    params["host"] = ",".join([host] * len(ordered))
    params["hostaddr"] = ",".join(ordered)
    params["port"] = str(port)
    return make_conninfo("", **params)


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
    with psycopg.connect(ipv4_first(dsn), row_factory=dict_row) as conn:
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
