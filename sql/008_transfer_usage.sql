-- GridCast — 008 Transfer accounting
-- Idempotent.
--
-- NFR-13. Neon's free tier meters bytes read out of the database, and on
-- 2026-08-17 the allowance ran out with nothing watching it: no counter, no
-- trend, no warning. The first signal was a refused connection, by which point
-- the pipeline had stopped writing to the register.
--
-- One row per job invocation. The value is the trend rather than the total —
-- a query that starts reading ten times more than it did is invisible until
-- something is adding it up, and that is the failure this table exists to make
-- loud rather than the ceiling itself.
--
-- In `landing` beside run_log rather than in `register`: this is operational
-- telemetry, not evidence. Nothing about a forecast's standing depends on it,
-- and unlike the register it is safe to delete. It is deliberately NOT added
-- to run_log, whose rows_read column counts records returned by the upstream
-- HTTP APIs; overloading it would break a documented meaning and make two
-- unrelated quantities share a column.

CREATE TABLE IF NOT EXISTS landing.db_transfer (
    transfer_id     bigserial   PRIMARY KEY,

    -- Null for jobs that run outside a RunContext, which is most of the read-
    -- only ones. Recorded when available so a spike can be tied to the
    -- ingestion run that caused it.
    run_id          uuid,
    job             text        NOT NULL,
    recorded_at_utc timestamptz NOT NULL DEFAULT now(),

    queries         integer     NOT NULL,
    rows_returned   bigint      NOT NULL,

    -- ESTIMATED, and named so nobody has to read the module to find out.
    -- Measured from the width of the values returned, not from the wire: it
    -- sees neither protocol framing nor TLS nor compression, and it will
    -- disagree with the provider's own figure. Good enough to catch a
    -- regression on the day it lands, which is what it is for.
    bytes_estimated bigint      NOT NULL,

    code_commit     text        NOT NULL,

    CONSTRAINT db_transfer_counts_sane
        CHECK (queries >= 0 AND rows_returned >= 0 AND bytes_estimated >= 0)
);

COMMENT ON TABLE landing.db_transfer IS
    'Estimated database egress per job invocation (NFR-13). Operational telemetry, not evidence — safe to prune.';
COMMENT ON COLUMN landing.db_transfer.bytes_estimated IS
    'Estimated from returned value widths, not measured on the wire. Will disagree with the provider figure. Exists to expose a trend, not to report remaining allowance.';

-- The only access pattern: everything since the billing period began.
CREATE INDEX IF NOT EXISTS db_transfer_recorded_idx
    ON landing.db_transfer (recorded_at_utc DESC);

-- Deletable, unlike the register. Retention is somebody's future decision and
-- the grant should not be the thing that prevents it.
GRANT SELECT, INSERT, DELETE ON landing.db_transfer TO gridcast_app;
GRANT SELECT ON landing.db_transfer TO gridcast_readonly;
GRANT USAGE, SELECT ON SEQUENCE landing.db_transfer_transfer_id_seq TO gridcast_app;
