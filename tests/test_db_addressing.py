"""The serving API must reach the database over IPv4 when IPv6 has no route.

Neon publishes both A and AAAA records, psycopg tries the resolver's addresses
in the order they arrive, and the Render container has no IPv6 route out. That
combination took the serving API down for a day while the database, the role
and the URL were all correct — the connection never got far enough to send a
credential, so the status page could only report an unrecognised fault.

The second day went to psycopg raising the *last* attempt's failure: with both
families resolved, the message always named an IPv6 address, whatever the IPv4
attempt had actually said. Hence the tests below about which failure surfaces.

These tests pin the ordering and the reporting rather than the outcome, because
the outcome depends on a network the test does not have.
"""

from __future__ import annotations

import logging
import socket

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from gridcast import db
from gridcast.db import resolve_attempts

DSN = "postgresql://role:pw@db.example.com:5432/neondb?sslmode=require"

V4 = ("203.0.113.10", "203.0.113.11")
V6 = ("2001:db8::1", "2001:db8::2")


def _answers(*addresses: tuple[int, str]) -> list[tuple]:
    """Shape a getaddrinfo() reply: (family, type, proto, canonname, sockaddr)."""
    return [
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 5432))
        for family, address in addresses
    ]


def _addresses(dsn_list: list[str]) -> list[str]:
    return [str(conninfo_to_dict(dsn)["hostaddr"]) for dsn in dsn_list]


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch):
    def _install(*addresses: tuple[int, str]) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _answers(*addresses))

    return _install


def test_ipv4_is_tried_before_ipv6(resolver) -> None:
    """The whole point: an unroutable IPv6 address must not be attempt one."""
    resolver(
        (socket.AF_INET6, V6[0]),
        (socket.AF_INET, V4[0]),
        (socket.AF_INET6, V6[1]),
        (socket.AF_INET, V4[1]),
    )

    assert _addresses(resolve_attempts(DSN)) == [V4[0], V4[1], V6[0], V6[1]]


def test_every_address_survives_the_reordering(resolver) -> None:
    """Reorder attempts, never drop them.

    Preferring IPv4 is a guess about the network. Discarding IPv6 would make it
    a decision, and would strand a host that is genuinely only reachable that
    way.
    """
    resolver((socket.AF_INET6, V6[0]), (socket.AF_INET, V4[0]))

    assert set(_addresses(resolve_attempts(DSN))) == {V4[0], V6[0]}


def test_the_hostname_is_kept_alongside_every_address(resolver) -> None:
    """Neon routes on SNI. An address with no name reaches the proxy and is
    refused in a way that looks exactly like a bad password."""
    resolver((socket.AF_INET6, V6[0]), (socket.AF_INET, V4[0]))

    for attempt in resolve_attempts(DSN):
        params = conninfo_to_dict(attempt)
        assert params["host"] == "db.example.com"
        assert params["sslmode"] == "require"
        assert params["user"] == "role"
        assert params["dbname"] == "neondb"


def test_an_ipv6_only_host_is_still_tried(resolver) -> None:
    """Preferring IPv4 must not mean refusing to use IPv6."""
    resolver((socket.AF_INET6, V6[0]))

    assert _addresses(resolve_attempts(DSN)) == [V6[0]]


def test_a_resolver_failure_is_left_to_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """psycopg reports DNS failures in its own words, and the status page
    classifies those words. Raising here would replace a diagnosis with a
    stack trace from the wrong layer."""

    def explode(*args: object, **kwargs: object) -> None:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", explode)

    assert resolve_attempts(DSN) == [DSN]


def test_an_explicit_hostaddr_is_respected() -> None:
    """A caller who pinned an address meant it."""
    pinned = DSN + "&hostaddr=203.0.113.99"

    assert resolve_attempts(pinned) == [pinned]


def test_a_unix_socket_is_left_alone() -> None:
    socket_dsn = "postgresql:///neondb?host=/var/run/postgresql"

    assert resolve_attempts(socket_dsn) == [socket_dsn]


def test_the_first_failure_is_raised_not_the_last(
    resolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug that cost the second day.

    psycopg tries every address and raises the last failure, so on a container
    with no IPv6 the message named an IPv6 address no matter what the IPv4
    attempt said. The IPv4 attempt is the one worth reporting.
    """
    resolver((socket.AF_INET, V4[0]), (socket.AF_INET6, V6[0]))

    def refuse(dsn: str, **kwargs: object) -> None:
        address = conninfo_to_dict(dsn)["hostaddr"]
        raise psycopg.OperationalError(f"attempt against {address} failed")

    monkeypatch.setattr(psycopg, "connect", refuse)

    with pytest.raises(psycopg.OperationalError) as raised:
        db._open(DSN)

    assert V4[0] in str(raised.value)
    assert V6[0] not in str(raised.value)


def test_every_failed_address_reaches_the_log(
    resolver, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The raised exception names one address; the operator needs all of them."""
    resolver((socket.AF_INET, V4[0]), (socket.AF_INET6, V6[0]))

    def refuse(dsn: str, **kwargs: object) -> None:
        raise psycopg.OperationalError("Network is unreachable")

    monkeypatch.setattr(psycopg, "connect", refuse)

    with caplog.at_level(logging.ERROR), pytest.raises(psycopg.OperationalError):
        db._open(DSN)

    assert V4[0] in caplog.text
    assert V6[0] in caplog.text
    assert "pw" not in caplog.text


def test_a_working_address_short_circuits_the_rest(
    resolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPv6 must never be dialled once IPv4 has answered."""
    resolver((socket.AF_INET, V4[0]), (socket.AF_INET6, V6[0]))
    dialled: list[str] = []

    def answer(dsn: str, **kwargs: object) -> str:
        dialled.append(str(conninfo_to_dict(dsn)["hostaddr"]))
        return "connection"

    monkeypatch.setattr(psycopg, "connect", answer)

    assert db._open(DSN) == "connection"
    assert dialled == [V4[0]]
