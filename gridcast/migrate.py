"""Apply the SQL files in ``sql/`` in filename order.

Every file is written to be idempotent, so this is safe to run on every deploy
and safe to run twice. There is no down-migration: the register is append-only,
and a rollback that can drop it would be a hole in the guarantee.

Usage::

    python -m gridcast.migrate
"""

from __future__ import annotations

import sys
from pathlib import Path

from gridcast.db import connect

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def sql_files() -> list[Path]:
    return sorted(SQL_DIR.glob("*.sql"))


def main() -> int:
    files = sql_files()
    if not files:
        print(f"No SQL files found in {SQL_DIR}", file=sys.stderr)
        return 1

    with connect() as conn:
        for path in files:
            statement = path.read_text(encoding="utf-8")
            print(f"applying {path.name} ... ", end="", flush=True)
            with conn.cursor() as cur:
                cur.execute(statement)
            print("ok")

    print(f"{len(files)} file(s) applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
