"""Test-suite guards.

The database-backed tests write. Some of them write to the forecast register,
and the register tests specifically prove that the application role *cannot*
delete what it inserts — so their rows are permanent by design.

That is fine against a local database and unacceptable against production, and
nothing stopped the second case until it happened: during M5 verification the
suite was run with GRIDCAST_DATABASE_URL pointing at Neon, and four `test-probe`
forecasts landed in the live evidential register.

They did no harm — no seal existed to invalidate, the landing fixtures cleaned
up after themselves, and nothing reached the marts. But the register is the one
table in this project whose value depends on containing only real forecasts, and
a test suite that can reach it is a loaded gun on the desk.

This refuses to run rather than skipping. A skip is the wrong signal: it looks
like the tests passed, and the person who pointed at production needs to know
immediately, not read a summary line.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", ""}


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Abort collection outright if the target database is not local."""
    url = os.environ.get("GRIDCAST_DATABASE_URL", "")
    if not url:
        return

    host = (urlsplit(url).hostname or "").lower()
    if host in LOCAL_HOSTS:
        return

    raise pytest.UsageError(
        f"\n\nREFUSING TO RUN: GRIDCAST_DATABASE_URL points at '{host}', which is "
        f"not a local database.\n\n"
        "The database tests write, and the register tests insert rows the "
        "application role\ncannot delete — by design, because that is what they "
        "prove. Running them against\na real warehouse puts test forecasts into "
        "the evidential register.\n\n"
        "This has happened once already. Point GRIDCAST_DATABASE_URL at "
        "localhost, or unset\nit to skip the database tests entirely.\n"
    )
