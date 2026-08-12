"""Integrity seals over the forecast register (SRS FR-19).

    python -m gridcast.seal --audit    # verify existing seals
    python -m gridcast.seal --seal     # seal any newly closed month
    python -m gridcast.seal            # both

The register is append-only because the application role has no UPDATE or
DELETE permission on it. That is a strong guarantee and it has one weakness:
believing it requires trusting whoever holds the database.

Seals remove that requirement. Once a month closes, a hash over every forecast
row in it is computed, stored, and **committed to git as seals/YYYY-MM.json**.
Anyone can then recompute the hash from the live API's published forecasts and
compare it against a file whose history is public and timestamped. Checking that
GridCast has not rewritten its own past needs no access to anything private.

A mismatch is never smoothed over. It fails the job, writes a permanent audit
row, and raises an alert — including when the mismatch is presumably innocent,
because a seal that tolerates innocent mismatches provides no evidence about the
guilty ones.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from gridcast.config import get_settings
from gridcast.db import connect

SEALS_DIR = Path(__file__).resolve().parent.parent / "seals"

# Months are UTC months, matching the register's index expression. A month
# boundary that moved with a server's timezone would hash a different set of
# rows on a different machine and produce a false mismatch.
CLOSED_MONTHS_SQL = """
    SELECT date_trunc('month', run_at_utc AT TIME ZONE 'UTC')::date AS period_month,
           count(*)                                                 AS row_count,
           sha256(string_agg(encode(row_hash, 'hex'), '' ORDER BY forecast_id)::bytea) AS seal_hash
      FROM register.reg_forecast_point
     WHERE date_trunc('month', run_at_utc AT TIME ZONE 'UTC')
         < date_trunc('month', now() AT TIME ZONE 'UTC')
     GROUP BY 1
     ORDER BY 1
"""


def compute_month_seals() -> list[dict]:
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(CLOSED_MONTHS_SQL)
        return cur.fetchall()


def compute_seal_for(period_month) -> dict:
    """Recompute the hash for one specific month, closed or not.

    The audit must verify whatever has actually been sealed, not only what it
    would seal today. Restricting recomputation to closed months would make any
    seal over the current month fail permanently — reporting tampering where
    there is none, which is the fastest way to teach everyone to ignore the
    alarm.
    """
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS row_count,
                   sha256(string_agg(encode(row_hash, 'hex'), '' ORDER BY forecast_id)::bytea)
                       AS seal_hash
              FROM register.reg_forecast_point
             WHERE date_trunc('month', run_at_utc AT TIME ZONE 'UTC')::date = %s
            """,
            (period_month,),
        )
        return cur.fetchone()


def stored_seals() -> dict:
    settings = get_settings()
    with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT period_month, row_count, seal_hash FROM register.reg_forecast_seal")
        return {row["period_month"]: row for row in cur.fetchall()}


def seal_new_months() -> int:
    """Seal any closed month that has not been sealed yet."""
    settings = get_settings()
    computed = compute_month_seals()
    existing = stored_seals()
    sealed = 0

    SEALS_DIR.mkdir(exist_ok=True)

    for month in computed:
        if month["period_month"] in existing:
            continue

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO register.reg_forecast_seal
                    (period_month, row_count, seal_hash, sealed_by_commit)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (period_month) DO NOTHING
                """,
                (
                    month["period_month"],
                    month["row_count"],
                    month["seal_hash"],
                    settings.build_id,
                ),
            )

        path = SEALS_DIR / f"{month['period_month']:%Y-%m}.json"
        path.write_text(
            json.dumps(
                {
                    "period_month": f"{month['period_month']:%Y-%m}",
                    "row_count": month["row_count"],
                    "seal_hash": month["seal_hash"].hex(),
                    "sealed_at_utc": datetime.now(UTC).isoformat(),
                    "sealed_by_commit": settings.build_id,
                    "algorithm": (
                        "sha256 over the concatenated hex row_hash values of every "
                        "forecast issued in this UTC month, ordered by forecast_id"
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"  sealed {month['period_month']:%Y-%m}: {month['row_count']:,} rows -> {path.name}")
        sealed += 1

    return sealed


def audit() -> int:
    """Recompute every stored seal and compare. Returns the number of failures."""
    existing = stored_seals()
    failures = 0

    if not existing:
        print("  no sealed months yet — nothing to audit")
        return 0

    for period_month, stored in sorted(existing.items()):
        fresh = compute_seal_for(period_month)
        observed_hash = fresh["seal_hash"] if fresh and fresh["seal_hash"] else b""
        observed_count = fresh["row_count"] if fresh else 0
        passed = observed_hash == stored["seal_hash"] and observed_count == stored["row_count"]

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO register.reg_seal_audit
                    (period_month, expected_hash, observed_hash,
                     expected_count, observed_count, passed)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    period_month,
                    stored["seal_hash"],
                    observed_hash,
                    stored["row_count"],
                    observed_count,
                    passed,
                ),
            )

        if passed:
            print(f"  {period_month:%Y-%m}  OK    {observed_count:,} rows")
        else:
            failures += 1
            print(
                f"  {period_month:%Y-%m}  FAIL  expected {stored['row_count']:,} rows "
                f"/ {stored['seal_hash'].hex()[:16]}…, "
                f"observed {observed_count:,} / {observed_hash.hex()[:16]}…"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seal", action="store_true", help="Seal newly closed months only")
    parser.add_argument("--audit", action="store_true", help="Verify existing seals only")
    args = parser.parse_args()

    do_seal = args.seal or not args.audit
    do_audit = args.audit or not args.seal

    settings = get_settings()
    print(f"database: {settings.database_url.split('@')[-1].split('?')[0] or 'NOT CONFIGURED'}")

    if do_seal:
        print("sealing closed months:")
        count = seal_new_months()
        if count == 0:
            print("  no newly closed months to seal")

    if do_audit:
        print("auditing seals:")
        failures = audit()
        if failures:
            # Loud, and non-zero. A seal mismatch means either the register was
            # altered or the sealing procedure is broken, and both must stop the
            # pipeline rather than be noted in a log nobody reads.
            print(
                f"\n::error title=Register integrity::{failures} sealed month(s) do not match. "
                "The forecast register has changed after sealing, or the seal is wrong. "
                "Neither is acceptable."
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
