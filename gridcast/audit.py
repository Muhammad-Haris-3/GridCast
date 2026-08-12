"""Run the committed audit queries and print their evidence.

    python -m gridcast.audit              # all
    python -m gridcast.audit --only B     # one family

Every figure this project publishes about its own data quality comes from a
file in ``audit/``, run by this. Nothing is quoted from a notebook cell that no
longer exists — which is the difference between a finding and a recollection.

Queries are numbered by family:

    A   coverage and gaps
    B   scoreability, versioning and the ESO index
    C   generation mix
    D   publication lag
    E   weather (pending the weather backfill)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from gridcast.config import get_settings
from gridcast.db import connect

AUDIT_DIR = Path(__file__).resolve().parent.parent / "audit"

# Queries whose data has not landed yet. Listed rather than silently skipped:
# an audit that quietly omits what it could not check is worse than one that
# says so, because the omission looks like a clean result.
PENDING: dict[str, str] = {
    "E02": "needs lnd_om_vintage and lnd_ci_genmix",
    "F01": "needs a full lnd_ex_demand backfill for a holiday comparison",
}


def query_files(prefix: str | None) -> list[Path]:
    files = sorted(AUDIT_DIR.glob("*.sql"))
    if prefix:
        files = [f for f in files if f.name.upper().startswith(prefix.upper())]
    return files


def describe(path: Path) -> str:
    """The first comment line of a query is its title."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("--"):
            return line.lstrip("- ").strip()
    return path.stem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="Run one family, e.g. B")
    parser.add_argument("--list", action="store_true", help="List queries without running")
    args = parser.parse_args()

    files = query_files(args.only)
    if not files:
        print(f"No audit queries found in {AUDIT_DIR}")
        return 1

    if args.list:
        for path in files:
            marker = "  (pending)" if path.stem.split("_")[0] in PENDING else ""
            print(f"  {path.stem:<34} {describe(path)}{marker}")
        return 0

    settings = get_settings()
    print(f"database: {settings.database_url.split('@')[-1].split('?')[0]}")

    failures = 0
    for path in files:
        code = path.stem.split("_")[0]
        print(f"\n{'=' * 78}\n{path.stem}\n{describe(path)}\n{'=' * 78}")

        if code in PENDING:
            print(f"  SKIPPED — {PENDING[code]}")
            continue

        try:
            # The pipeline URL, opened in a read-only session — NOT the serving
            # URL. `connect(readonly=True)` alone would resolve to
            # GRIDCAST_READONLY_DATABASE_URL, which on a developer machine
            # usually points at a local database. That silently answered the
            # first run of this audit from 239 local rows instead of 144,763 in
            # production, and every number looked perfectly plausible.
            #
            # An analysis tool reading a different database than it was told to
            # is the exact failure this directory exists to catch.
            with connect(url=settings.database_url, readonly=True) as conn, conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            failures += 1
            continue

        if not rows:
            print("  (no rows)")
            continue

        headers = list(rows[0].keys())
        widths = [max(len(h), max(len(str(r[h])) for r in rows)) for h in headers]
        print("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
        print("  " + "  ".join("-" * w for w in widths))
        for row in rows[:40]:
            print(
                "  " + "  ".join(str(row[h]).ljust(w) for h, w in zip(headers, widths, strict=True))
            )
        if len(rows) > 40:
            print(f"  ... {len(rows) - 40:,} more row(s)")

    if failures:
        print(f"\n{failures} quer(y/ies) failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
