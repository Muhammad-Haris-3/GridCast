"""Check database table sizes — diagnostic script."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gridcast.db import fetch_all

rows = fetch_all("""
    SELECT
        schemaname,
        tablename,
        pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) as total_size,
        pg_total_relation_size(schemaname || '.' || tablename) as size_bytes
    FROM pg_tables
    WHERE schemaname IN ('landing', 'marts', 'staging', 'register')
    ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC
""")

total = 0
print(f"{'Table':45s} {'Size':>12s}   {'Bytes':>14s}")
print("-" * 75)
for r in rows:
    name = f"{r['schemaname']}.{r['tablename']}"
    total += r["size_bytes"]
    print(f"{name:45s} {r['total_size']:>12s}   {r['size_bytes']:>14,}")

print("-" * 75)
print(f"{'TOTAL':45s} {total / 1024 / 1024:>9.1f} MB   {total:>14,}")

# Also check database size
db_size = fetch_all(
    "SELECT pg_size_pretty(pg_database_size(current_database())) as db_size, "
    "pg_database_size(current_database()) as db_bytes"
)
print(f"\nDatabase total: {db_size[0]['db_size']}  ({db_size[0]['db_bytes']:,} bytes)")

# Check if fct_weather_hour already exists
try:
    count = fetch_all("SELECT count(*) as n FROM marts.fct_weather_hour")
    print(f"\nfct_weather_hour rows: {count[0]['n']:,}")
except Exception as e:
    print(f"\nfct_weather_hour: {e}")
