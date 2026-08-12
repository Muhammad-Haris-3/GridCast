-- GridCast — 001 Schemas
-- Idempotent. Safe to re-run on every deploy.
--
-- Four layers, each with exactly one job (design doc section 1).

-- ---------------------------------------------------------------------------
-- Pin the database to UTC.
--
-- Design doc section 2: all time arithmetic is UTC; local time is presentation
-- only. That rule has to hold for every client, not just the ones that remember
-- to set it — dbt, psql and psycopg all connect independently. Setting it on
-- the database itself makes the rule structural.
--
-- It matters most for the seal: date_trunc() over a timestamptz uses the
-- session timezone, so a connection in a different zone would partition the
-- register by different month boundaries and compute a mismatching hash over
-- identical data.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET timezone TO ''UTC''', current_database());
END
$$;

CREATE SCHEMA IF NOT EXISTS landing;   -- append-only, Python-written
CREATE SCHEMA IF NOT EXISTS staging;   -- views, dbt-written
CREATE SCHEMA IF NOT EXISTS marts;     -- incremental tables, dbt-written
CREATE SCHEMA IF NOT EXISTS register;  -- append-only, Python-written, dbt-READ-only

COMMENT ON SCHEMA landing  IS 'Source-faithful API payloads. Append-only: a row is written only when the payload for a key differs from the last stored payload. Idempotency and revision history fall out of the same mechanism.';
COMMENT ON SCHEMA staging  IS 'Typed, UTC-normalised views. One staging model reads exactly one landing table. Staging never joins.';
COMMENT ON SCHEMA marts    IS 'Dimensional model. Incremental with a deliberate lookback window, because actuals arrive late.';
COMMENT ON SCHEMA register IS 'The forecast register. Append-only and NOT a dbt layer: a forecast is evidence of what was believed at a moment in time, and anything dbt can rebuild is not evidence.';
