"""The single write path into the landing layer.

Every source writes through :func:`write_records`, and it does exactly one
thing: insert a row only when the payload for that key differs from the last
payload already stored.

That one rule delivers three requirements at once:

* **Idempotency (FR-3).** Re-running an unchanged window writes nothing.
* **Revision history (FR-9).** A revised value is a new row, not an overwrite,
  so the old value survives.
* **Knowability (FR-15).** ``fetched_at_utc`` on each row is the instant we
  first held that value, which is the boundary every point-in-time feature is
  built against.

None of those needed a separate mechanism. They are the same mechanism.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from gridcast.sources.base import Record, SourceSpec


def write_records(
    conn: Connection,
    spec: SourceSpec,
    records: Sequence[Record],
    *,
    run_id: uuid.UUID,
) -> int:
    """Insert the records whose payload differs from what is already stored.

    Returns the number of rows actually written, which is normally far below
    the number read. That gap is the mechanism working, not data loss.
    """
    if not records:
        return 0

    schema, table = spec.landing_table.split(".")
    key_names = spec.key_names
    ident = {name: sql.Identifier(name) for name in key_names}

    # The candidate rows, cast explicitly. Without the casts a VALUES list is
    # inferred as text, and text never equals timestamptz, so every comparison
    # below would miss and every row would be written on every run.
    value_row = sql.SQL("({})").format(
        sql.SQL(", ").join(
            [sql.SQL("%s::{}").format(sql.SQL(pg_type)) for _, pg_type in spec.key_columns]
            + [sql.SQL("%s::jsonb"), sql.SQL("%s::bytea")]
        )
    )
    values = sql.SQL(", ").join([value_row] * len(records))

    params: list[Any] = []
    for record in records:
        params.extend(record.key[name] for name in key_names)
        params.append(Jsonb(record.payload))
        params.append(record.payload_hash)

    time_values = [r.key[spec.time_column] for r in records]
    lo, hi = min(time_values), max(time_values)

    statement = sql.SQL("""
        INSERT INTO {schema}.{table} ({key_cols}, payload, payload_hash, run_id)
        SELECT {v_key_cols}, v.payload, v.payload_hash, %s
          FROM (VALUES {values}) AS v ({key_cols}, payload, payload_hash)
         WHERE NOT EXISTS (
               SELECT 1
                 FROM (
                      SELECT DISTINCT ON ({key_cols}) {key_cols}, payload_hash
                        FROM {schema}.{table}
                       WHERE {time_col} BETWEEN %s AND %s
                       ORDER BY {key_cols}, fetched_at_utc DESC
                      ) latest
                WHERE {key_match}
                  AND latest.payload_hash = v.payload_hash
               )
    """).format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        key_cols=sql.SQL(", ").join(ident[name] for name in key_names),
        v_key_cols=sql.SQL(", ").join(sql.SQL("v.{}").format(ident[n]) for n in key_names),
        values=values,
        time_col=ident[spec.time_column],
        key_match=sql.SQL(" AND ").join(
            sql.SQL("latest.{col} = v.{col}").format(col=ident[n]) for n in key_names
        ),
    )

    with conn.cursor() as cur:
        cur.execute(statement, [run_id, *params, lo, hi])
        return cur.rowcount
