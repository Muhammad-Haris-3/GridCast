"""One-command database setup for a fresh Postgres (Neon, or anything else).

Takes the owner connection string as an argument, and does everything M0 needs:

    1. connects and reports what it connected to
    2. applies sql/001-004
    3. gives gridcast_readonly a login and a password
    4. builds the dbt warehouse
    5. verifies the append-only guarantee actually holds on THIS server
    6. prints the read-only connection string to paste into Render

Everything is idempotent, so re-running after a failure is safe and expected.

Usage:

    python scripts/bootstrap_database.py "postgresql://user:pw@host/db?sslmode=require"

Nothing is written to disk. The generated password is printed once — if you
lose it, re-run with --readonly-password to set a new one.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parent.parent
SQL_DIR = REPO / "sql"
DBT_DIR = REPO / "dbt_gridcast"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}   {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET} {msg}")


def step(n: int, title: str) -> None:
    print(f"\n{'=' * 70}\nSTEP {n} - {title}\n{'=' * 70}")


def swap_credentials(url: str, user: str, password: str, *, pooled: bool = False) -> str:
    """Rebuild a connection string with different credentials."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if pooled and "-pooler" not in host:
        # Neon's pooled endpoint inserts -pooler into the endpoint id.
        host = re.sub(r"^(ep-[^.]+)", r"\1-pooler", host)
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(
        (parts.scheme, f"{user}:{password}@{host}{port}", parts.path, parts.query, "")
    )


def pg_env_from_url(url: str) -> dict[str, str]:
    """Derive the PG* variables dbt reads from a connection string."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return {
        "PGHOST": parts.hostname or "localhost",
        "PGPORT": str(parts.port or 5432),
        "PGUSER": parts.username or "",
        "PGPASSWORD": parts.password or "",
        "PGDATABASE": (parts.path or "/postgres").lstrip("/"),
        "PGSSLMODE": query.get("sslmode", ["prefer"])[0],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database_url", help="Owner connection string (the DIRECT one, not pooled)")
    parser.add_argument(
        "--readonly-password",
        default=None,
        help="Password for gridcast_readonly. Generated if omitted.",
    )
    parser.add_argument("--skip-dbt", action="store_true", help="Skip the warehouse build")
    args = parser.parse_args()

    url = args.database_url.strip().strip('"').strip("'")
    ro_password = args.readonly_password or secrets.token_urlsafe(24)

    # ---------------------------------------------------------------- step 1
    step(1, "Connect")
    try:
        with psycopg.connect(url, connect_timeout=30) as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, version()")
            row = cur.fetchone()
            assert row is not None
            database, user, version = row
    except Exception as exc:
        fail(f"Could not connect: {type(exc).__name__}: {exc}")
        print(
            f"\n{DIM}If this is Neon, check the string ends with ?sslmode=require "
            f"and that you used the DIRECT (non-pooler) endpoint.{RESET}"
        )
        return 1

    ok(f"database = {database}")
    ok(f"user     = {user}")
    ok(f"server   = {version.split(',')[0]}")

    # ---------------------------------------------------------------- step 2
    step(2, "Apply schema")
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        fail(f"No .sql files found in {SQL_DIR}")
        return 1
    try:
        with psycopg.connect(url) as conn:
            for path in files:
                with conn.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
                ok(f"applied {path.name}")
    except Exception as exc:
        fail(f"{path.name}: {type(exc).__name__}: {exc}")
        if "permission denied" in str(exc).lower() or "must be owner" in str(exc).lower():
            print(
                f"\n{DIM}This role lacks the rights to create roles or alter the "
                f"database. On Neon, use the default owner role.{RESET}"
            )
        return 1

    # ---------------------------------------------------------------- step 3
    step(3, "Give gridcast_readonly a login")
    try:
        with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE gridcast_readonly WITH LOGIN PASSWORD {}").format(
                    sql.Literal(ro_password)
                )
            )
        ok("gridcast_readonly can now log in")
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")
        return 1

    # ---------------------------------------------------------------- step 4
    if args.skip_dbt:
        step(4, "Build warehouse - SKIPPED")
        warn("The spine will be empty and /v1/status will show no periods.")
    else:
        step(4, "Build warehouse")
        env = {**os.environ, **pg_env_from_url(url)}
        result = subprocess.run(
            [sys.executable, "-m", "dbt.cli.main", "build", "--profiles-dir", "."],
            cwd=DBT_DIR,
            env=env,
            capture_output=True,
            text=True,
        )
        tail = (result.stdout or result.stderr).strip().splitlines()[-1:]
        if result.returncode != 0:
            fail("dbt build failed. Last lines:")
            print("\n".join((result.stdout or result.stderr).strip().splitlines()[-15:]))
            return 1
        ok(f"dbt {tail[0].split('  ')[-1] if tail else 'completed'}")

    # ---------------------------------------------------------------- step 5
    step(5, "Verify")
    ro_url = swap_credentials(url, "gridcast_readonly", ro_password)

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # Check what the zone DOES, not what it is called.
        #
        # Postgres reports the zero-offset zone as 'UTC' on some servers and
        # 'GMT' on others (Neon); both are correct and identical. Comparing the
        # name would fail on a perfectly good server.
        #
        # But a pure offset check is not enough either: Europe/London is also
        # zero-offset in January and then shifts by an hour in July. So the zone
        # is probed at two instants six months apart, and must be zero at both.
        cur.execute("""
            SELECT current_setting('TimeZone') AS zone,
                   (timestamptz '2026-01-15 12:00:00+00' AT TIME ZONE current_setting('TimeZone'))
                       = timestamp '2026-01-15 12:00:00' AS winter_zero,
                   (timestamptz '2026-07-15 12:00:00+00' AT TIME ZONE current_setting('TimeZone'))
                       = timestamp '2026-07-15 12:00:00' AS summer_zero
        """)
        zone, winter_zero, summer_zero = cur.fetchone()
        if winter_zero and summer_zero:
            ok(f"session timezone = {zone} (zero offset year-round)")
        else:
            fail(f"session timezone = {zone} (offset is not zero year-round)")

        # Separately, confirm ALTER DATABASE actually persisted. A pooled
        # connection can be handed a backend that started before it ran, so the
        # session value alone does not prove the default was stored.
        cur.execute("""
            SELECT unnest(setconfig)
              FROM pg_db_role_setting s
              JOIN pg_database d ON d.oid = s.setdatabase
             WHERE d.datname = current_database()
        """)
        stored = [r[0] for r in cur.fetchall()]
        if any(c.lower() == "timezone=utc" for c in stored):
            ok("database default timezone = UTC (persisted)")
        else:
            fail(f"database default timezone not stored; found {stored or 'nothing'}")

        cur.execute("SELECT count(*) FROM marts.dim_settlement_period")
        periods = cur.fetchone()[0]
        (ok if periods > 100_000 else warn)(f"spine periods = {periods:,}")

    try:
        with psycopg.connect(ro_url, connect_timeout=30) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM marts.dim_settlement_period")
            ok(f"read-only role can SELECT ({cur.fetchone()[0]:,} rows)")
    except Exception as exc:
        fail(f"read-only role cannot connect: {type(exc).__name__}: {exc}")
        return 1

    # The assertion the whole project rests on, checked on THIS server.
    try:
        with psycopg.connect(ro_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO register.reg_forecast_seal "
                "(period_month, row_count, seal_hash, sealed_by_commit) "
                "VALUES ('1999-01-01', 0, '\\x00', 'bootstrap-probe')"
            )
        fail("read-only role WROTE to the register. The append-only guarantee is void here.")
        return 1
    except psycopg.errors.InsufficientPrivilege:
        ok("read-only role is refused write access to the register")
    except Exception as exc:
        fail(f"unexpected error probing write access: {type(exc).__name__}: {exc}")
        return 1

    # ---------------------------------------------------------------- done
    print(f"\n{'=' * 70}\n{GREEN}DATABASE READY{RESET}\n{'=' * 70}")
    pooled_url = swap_credentials(url, "gridcast_readonly", ro_password, pooled=True)
    print("\nPaste this into Render as GRIDCAST_READONLY_DATABASE_URL:\n")
    print(f"  {pooled_url}\n")
    if pooled_url != ro_url:
        print(f"{DIM}That is Neon's pooled endpoint, which is what the API should use.")
        print(f"The direct endpoint, for migrations and dbt, is:{RESET}\n")
        print(f"  {ro_url}\n")
    print(f"{YELLOW}This password is shown once and stored nowhere.{RESET}")
    print(f"{YELLOW}Do NOT give Render the owner URL you passed to this script.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
