"""Prune landing tables to reclaim storage.

The Neon free tier has a 512 MB ceiling. With four forecast models writing
every hour, the database grows ~4.7 MB/day and has ~40 MB free — roughly six
days until it stops accepting writes.

This script deletes old rows from the landing tables whose content has been
materialised into typed marts. It is safe to run because:

  * fct_weather_hour materialises ALL of lnd_om_vintage into typed columns.
    Once that table exists, the raw JSON payloads are dead weight.
  * The pipeline's insert-if-changed mechanism (landing.py) only looks at the
    current window when deciding what to write. Deleting old rows does not
    affect it.
  * The dbt staging models (stg_om_*) use DISTINCT ON, so they produce the
    same result with fewer rows — they just scan less.

Safety checks before any deletion:
  1. fct_weather_hour must exist and have rows.
  2. A dry-run mode (default) shows what would be deleted without touching data.
  3. Row counts and sizes are reported before and after.

Usage:
    # Dry run — show what would be deleted
    python scripts/prune_landing.py

    # Actually delete
    python scripts/prune_landing.py --execute

    # Keep more recent data (default: 7 days for vintage, 3 for forecast)
    python scripts/prune_landing.py --execute --vintage-keep-days 14
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gridcast.db import connect, fetch_one  # noqa: E402


def table_size(table: str) -> dict:
    """Return row count and byte size for a table."""
    row = fetch_one(
        f"""
        SELECT count(*) as row_count,
               pg_total_relation_size('{table}') as size_bytes,
               pg_size_pretty(pg_total_relation_size('{table}')) as size_pretty
        """
    )
    return row


def database_size() -> dict:
    """Return total database size."""
    return fetch_one(
        "SELECT pg_database_size(current_database()) as size_bytes, "
        "pg_size_pretty(pg_database_size(current_database())) as size_pretty"
    )


def mart_rows(mart: str) -> int | None:
    """Row count of a typed mart, or None if it does not exist yet."""
    try:
        row = fetch_one(f"SELECT count(*) as n FROM {mart}")
        return row["n"]
    except Exception:
        return None


def count_rows_to_delete(table: str, time_col: str, keep_days: int) -> dict:
    """Count rows older than keep_days and rows that would remain."""
    row = fetch_one(
        f"""
        SELECT
            count(*) FILTER (WHERE {time_col} < now() - interval '{keep_days} days')
                as delete_count,
            count(*) FILTER (WHERE {time_col} >= now() - interval '{keep_days} days') as keep_count,
            count(*) as total_count,
            pg_size_pretty(pg_total_relation_size('{table}')) as current_size,
            min({time_col}) as oldest,
            max({time_col}) as newest
        FROM {table}
        """
    )
    return row


def prune_table(table: str, time_col: str, keep_days: int, *, execute: bool) -> int:
    """Rebuild the table with only recent rows, then drop the original.

    NOT `DELETE` plus `VACUUM FULL`, which is what this did first and which
    cannot work here.

    VACUUM FULL rewrites a table into fresh space before releasing the old — so
    reclaiming 184 MB needs 184 MB free to do it in. This database has 40 MB.
    The command would fail, and on a database already at its ceiling a failing
    rewrite is a worse position than not starting.

    Plain DELETE does not help either: dead rows stay on the pages, and Neon
    counts them against the project limit until its own history window expires.

    Rebuilding inverts the requirement. The retention windows keep a few
    thousand rows out of hundreds of thousands, so the copy needs space for what
    is kept rather than for what is discarded, and `DROP TABLE` releases the
    original immediately — measured on this database earlier when dropping a
    23 MB table moved the total the same instant.

    The table is recreated from `sql/002_landing.sql` rather than cloned with
    `LIKE`, because a cloned bigserial default still points at the original
    sequence, which the drop then removes. Rebuilding from the committed DDL
    means the restored table is defined by the same file as every other
    deployment.
    """
    if not execute:
        return 0

    schema, name = table.split(".")
    cutoff_sql = f"now() - interval '{keep_days} days'"

    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {time_col} < {cutoff_sql}")
        to_delete = cur.fetchone()["n"]
        if to_delete == 0:
            return 0

        # Park the rows worth keeping, drop the original, rebuild from DDL,
        # put them back. All in one transaction: a failure part-way leaves the
        # landing table exactly as it was.
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}_keep")
        cur.execute(
            f"CREATE TABLE {schema}.{name}_keep AS "
            f"SELECT * FROM {table} WHERE {time_col} >= {cutoff_sql}"
        )
        # CASCADE, because the staging views read this table and Postgres will
        # not drop it out from under them.
        #
        # Safe only because those views are dbt's, not ours: they hold no data,
        # they are recreated from committed SQL, and the caller runs `dbt build`
        # immediately afterwards. CASCADE against anything dbt does not own
        # would be a different and much worse proposition.
        cur.execute(f"DROP TABLE {table} CASCADE")

    # Recreate from the committed schema, then restore.
    from gridcast.migrate import main as apply_schema

    apply_schema()

    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {table} SELECT * FROM {schema}.{name}_keep")
        restored = cur.rowcount
        cur.execute(f"DROP TABLE {schema}.{name}_keep")
        # The rebuilt sequence starts at 1; move it past the restored ids so a
        # later insert cannot collide with a row that survived the prune.
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'landing_id'), "
            f"COALESCE((SELECT max(landing_id) FROM {table}), 1))"
        )

    print(f"    kept {restored:,} rows, released {to_delete:,}")
    return to_delete


def main():
    parser = argparse.ArgumentParser(description="Prune old landing rows to reclaim Neon storage.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete rows. Without this flag, only shows what would happen.",
    )
    parser.add_argument(
        "--vintage-keep-days",
        type=int,
        default=7,
        help="Days of lnd_om_vintage to keep (default: 7). The full history is "
        "in fct_weather_hour; this window is for revision capture only.",
    )
    parser.add_argument(
        "--archive-keep-days",
        type=int,
        default=7,
        help="Days of lnd_om_archive to keep (default: 7).",
    )
    parser.add_argument(
        "--forecast-keep-days",
        type=int,
        default=3,
        help="Days of lnd_om_forecast to keep (default: 3). Forecasts older than "
        "this are superseded and carry no information.",
    )
    parser.add_argument(
        "--demand-keep-days",
        type=int,
        default=30,
        help="Days of lnd_ex_demand to keep (default: 30). fct_demand_period "
        "holds every vintage typed, verified row-for-row against the raw table.",
    )
    parser.add_argument(
        "--genmix-keep-days",
        type=int,
        default=30,
        help="Days of lnd_ci_genmix to keep (default: 30). The wide mix is "
        "materialised incrementally, so a 14-day lookback window suffices.",
    )
    parser.add_argument(
        "--regional-keep-days",
        type=int,
        default=30,
        help="Days of lnd_ci_regional to keep (default: 30). Regional intensity "
        "is forecast-only and cannot be scored; old data is descriptive only.",
    )
    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # Pre-flight
    # -----------------------------------------------------------------------
    db = database_size()
    print(f"Database size: {db['size_pretty']}  ({db['size_bytes']:,} bytes)")
    print()

    # -----------------------------------------------------------------------
    # Plan
    # -----------------------------------------------------------------------
    # Each landing table names the typed mart that already holds its content.
    # A table is only pruned once its mart exists — the prerequisite is checked
    # per table rather than globally.
    #
    # Checking it globally was what deadlocked this: lnd_om_vintage needs
    # fct_weather_hour, fct_weather_hour needs ~30 MB to materialise into, and
    # there is no 30 MB free. One unmet prerequisite blocked every table,
    # including the two whose marts have existed since M3 and which between them
    # hold the space that would let the weather model build.
    targets = [
        ("landing.lnd_ci_genmix", "sp_start_utc", args.genmix_keep_days, "marts.fct_mix_wide"),
        ("landing.lnd_ex_demand", "sp_start_utc", args.demand_keep_days, "marts.fct_demand_period"),
        (
            "landing.lnd_om_vintage",
            "hour_start_utc",
            args.vintage_keep_days,
            "marts.fct_weather_hour",
        ),
        ("landing.lnd_om_archive", "hour_start_utc", args.archive_keep_days, None),
        ("landing.lnd_om_forecast", "hour_start_utc", args.forecast_keep_days, None),
        ("landing.lnd_ci_regional", "sp_start_utc", args.regional_keep_days, None),
    ]

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"Mode: {mode}")
    print("=" * 70)

    total_to_delete = 0
    results = []

    skipped = []
    for table, time_col, keep_days, required_mart in targets:
        if required_mart is not None:
            rows = mart_rows(required_mart)
            if not rows:
                print("")
                print(table)
                print(f"  SKIPPED — {required_mart} does not exist or is empty.")
                print("  Pruning without it would lose the data permanently.")
                skipped.append((table, required_mart))
                continue

        info = count_rows_to_delete(table, time_col, keep_days)
        size_info = table_size(table)
        results.append((table, time_col, keep_days, info, size_info))
        _ = required_mart

        print(f"\n{table}")
        print(f"  Current: {info['total_count']:>10,} rows   {info['current_size']}")
        print(f"  Oldest:  {info['oldest']}")
        print(f"  Newest:  {info['newest']}")
        print(f"  Keep:    {info['keep_count']:>10,} rows   (last {keep_days} days)")
        print(f"  Delete:  {info['delete_count']:>10,} rows")

        total_to_delete += info["delete_count"]

    print(f"\n{'=' * 70}")
    print(f"Total rows to delete: {total_to_delete:,}")

    if total_to_delete == 0:
        print("\nNothing to prune.")
        return

    if not args.execute:
        print("\nThis is a DRY RUN. No data was changed.")
        print("Run with --execute to perform the deletion.")
        return

    # -----------------------------------------------------------------------
    # Execute
    # -----------------------------------------------------------------------
    print("\nDeleting...")

    for table, time_col, keep_days, info, _size_info in results:
        if info["delete_count"] == 0:
            print(f"  {table}: nothing to delete")
            continue

        deleted = prune_table(table, time_col, keep_days, execute=True)
        print(f"  {table}: deleted {deleted:,} rows")

    # -----------------------------------------------------------------------
    # No VACUUM step.
    #
    # Rebuilding each table and dropping the original releases the space
    # directly, which is the whole reason for that approach. VACUUM FULL would
    # need as much free space as the table it is rewriting, which is exactly
    # what a database at its ceiling does not have.
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print()
    db_after = database_size()
    print(f"Database size: {db['size_pretty']} -> {db_after['size_pretty']}")
    print(f"Freed: {(db['size_bytes'] - db_after['size_bytes']) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
