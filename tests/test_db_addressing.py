"""The serving API must reach the database over IPv4 when IPv6 has no route.

Neon publishes both A and AAAA records, psycopg tries the resolver's addresses
in the order they arrive, and the Render container has no IPv6 route out. That
combination took the serving API down for a day while the database, the role
and the URL were all correct — the connection never got far enough to send a
credential, so the status page could only report an unrecognised fault.

These tests pin the ordering rather than the outcome, because the outcome
depends on a network the test does not have.
"""

from __future__ import annotations

import socket

import pytest
from psycopg.conninfo import conninfo_to_dict

from gridcast.db import ipv4_first

DSN = "postgresql://role:pw@db.example.com:5432/neondb?sslmode=require"

V4 = ("203.0.113.10", "203.0.113.11")
V6 = ("2001:db8::1", "2001:db8::2")


def _answers(*addresses: tuple[int, str]) -> list[tuple]:
    """Shape a getaddrinfo() reply: (family, type, proto, canonname, sockaddr)."""
    return [
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 5432))
        for family, address in addresses
    ]


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
    params = conninfo_to_dict(ipv4_first(DSN))

    assert params["hostaddr"] == f"{V4[0]},{V4[1]},{V6[0]},{V6[1]}"


def test_every_address_survives_the_reordering(resolver) -> None:
    """Reorder attempts, never drop them.

    Preferring IPv4 is a guess about the network. Discarding IPv6 would make it
    a decision, and would strand a host that is genuinely only reachable that
    way.
    """
    resolver((socket.AF_INET6, V6[0]), (socket.AF_INET, V4[0]))
    params = conninfo_to_dict(ipv4_first(DSN))

    assert set(str(params["hostaddr"]).split(",")) == {V4[0], V6[0]}


def test_the_hostname_is_kept_alongside_every_address(resolver) -> None:
    """Neon routes on SNI. An address with no name reaches the proxy and is
    refused in a way that looks exactly like a bad password."""
    resolver((socket.AF_INET6, V6[0]), (socket.AF_INET, V4[0]))
    params = conninfo_to_dict(ipv4_first(DSN))

    assert params["host"] == "db.example.com,db.example.com"
    assert params["sslmode"] == "require"
    assert params["user"] == "role"
    assert params["dbname"] == "neondb"


def test_a_single_family_is_left_alone(resolver) -> None:
    """Nothing to reorder means nothing to change."""
    resolver((socket.AF_INET, V4[0]), (socket.AF_INET, V4[1]))

    assert ipv4_first(DSN) == DSN


def test_an_ipv6_only_host_is_left_alone(resolver) -> None:
    """Preferring IPv4 must not mean refusing to use IPv6."""
    resolver((socket.AF_INET6, V6[0]))

    assert ipv4_first(DSN) == DSN


def test_a_resolver_failure_is_left_to_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """psycopg reports DNS failures in its own words, and the status page
    classifies those words. Raising here would replace a diagnosis with a
    stack trace from the wrong layer."""

    def explode(*args: object, **kwargs: object) -> None:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", explode)

    assert ipv4_first(DSN) == DSN


def test_an_explicit_hostaddr_is_respected() -> None:
    """A caller who pinned an address meant it."""
    pinned = DSN + "&hostaddr=203.0.113.99"

    assert ipv4_first(pinned) == pinned


def test_a_unix_socket_is_left_alone() -> None:
    assert ipv4_first("postgresql:///neondb?host=/var/run/postgresql") == (
        "postgresql:///neondb?host=/var/run/postgresql"
    )
