"""Database access.

Thin helpers over psycopg3. No ORM: every query in this project is written to
be readable as SQL, because the SQL is part of the deliverable.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from gridcast.config import get_settings

logger = logging.getLogger(__name__)


def resolve_attempts(dsn: str) -> list[str]:
    """Expand a DSN into one DSN per address, IPv4 before IPv6.

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

    Every address is kept, as its own attempt, so this reorders and never
    drops: preferring IPv4 is a guess about the network, and discarding IPv6
    would make it a decision that strands a host reachable only that way.
    `host` travels with each address so TLS still carries the hostname — Neon
    routes on SNI, and an address with no name reaches the proxy and is refused
    in a way that reads exactly like a credential fault.

    One DSN per address rather than one DSN listing every address, because
    psycopg's own loop raises the *last* failure. With a host that publishes
    both families and a network that routes one, the last failure is always the
    unroutable family, and the attempt that actually mattered is never
    reported. :func:`connect` walks this list itself so it can say what each
    one said.

    Anything unexpected — no host, a literal address, a caller who already set
    hostaddr, a resolver failure — comes back as a single unchanged attempt, so
    psycopg keeps full responsibility for the cases this does not understand.
    """
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return [dsn]

    host = params.get("host")
    if not isinstance(host, str) or not host or params.get("hostaddr"):
        return [dsn]
    if host.startswith("/") or "," in host:
        return [dsn]

    port = params.get("port") or 5432
    try:
        answers = socket.getaddrinfo(
            host, int(port), proto=socket.IPPROTO_TCP, type=socket.SOCK_STREAM
        )
    except (OSError, ValueError):
        # Let psycopg resolve and report the failure in its own words.
        return [dsn]

    ordered: list[str] = []
    for family in (socket.AF_INET, socket.AF_INET6):
        for answer in answers:
            address = answer[4][0]
            if answer[0] == family and address not in ordered:
                ordered.append(address)

    if not ordered:
        return [dsn]

    return [make_conninfo("", **{**params, "hostaddr": address}) for address in ordered]


def _address_of(dsn: str) -> str:
    """The address an attempt will use, for logging. Never the credential."""
    try:
        return str(conninfo_to_dict(dsn).get("hostaddr") or "resolved by psycopg")
    except psycopg.ProgrammingError:
        return "unknown"


def _open(dsn: str) -> psycopg.Connection:
    """Open the first address that answers, and account for the ones that did not.

    psycopg tries every address and then raises the last failure. That is the
    right default and it was actively misleading here: it named an IPv6 address
    on a container with no IPv6, every time, whatever the IPv4 attempts had to
    say. A day went into reading that message.

    So each attempt is made here and each failure is kept. The log line carries
    all of them — Render's logs are private, and the address that failed is the
    single most useful fact when this happens again. The exception raised is the
    *first* failure rather than the last, because the list is ordered by which
    address is most likely to be the real one.
    """
    attempts = resolve_attempts(dsn)
    failures: list[tuple[str, Exception]] = []

    for attempt in attempts:
        try:
            return psycopg.connect(attempt, row_factory=dict_row)
        except psycopg.OperationalError as exc:
            failures.append((_address_of(attempt), exc))

    logger.error(
        "all %d connection attempts failed: %s",
        len(failures),
        "; ".join(f"{address}: {exc}".replace("\n", " ") for address, exc in failures),
    )
    raise failures[0][1]


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
    with _open(dsn) as conn:
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
