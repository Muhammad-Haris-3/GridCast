-- GridCast — 005 Backtest results
-- Idempotent.
--
-- A schema of its own, deliberately.
--
-- Backtest results and live scores must never be pooled (design 8.3), for two
-- independent reasons, both measured rather than assumed:
--
--   1. Backfilled rows carry reconstructed knowability. fetched_at_utc on a
--      backfilled row is when the backfill ran, so a backtest reasons about
--      availability by proxy rather than by record.
--
--   2. The ESO benchmark is not horizon-matched in history. Measured
--      2026-08-12: of 46 future periods held in the warehouse, 33 had their ESO
--      forecast revised within two hours. The stored value against a 2019
--      period is the ESO's final near-term forecast, not a 48-hour-ahead one,
--      so a backtest compares GridCast at 48 hours against the ESO at something
--      much shorter.
--
-- Keeping these in `backtest` rather than `register` means pooling them with
-- live results requires an explicit cross-schema join. Somebody may still do
-- it; they cannot do it by accident.

CREATE SCHEMA IF NOT EXISTS backtest;

COMMENT ON SCHEMA backtest IS
    'Rolling-origin backtest results. Approximate by construction — reconstructed vintages and a non-horizon-matched ESO benchmark. Never to be pooled with register scores.';

CREATE TABLE IF NOT EXISTS backtest.bt_run (
    bt_run_id     uuid        PRIMARY KEY,
    ran_at_utc    timestamptz NOT NULL DEFAULT now(),
    date_from     timestamptz NOT NULL,
    date_to       timestamptz NOT NULL,
    step_hours    integer     NOT NULL,
    embargo_hours integer     NOT NULL,
    mase_scale    numeric(10,4),
    code_commit   text        NOT NULL,

    CONSTRAINT bt_run_window_forward CHECK (date_to > date_from),

    -- An embargo of zero would let an origin train on actuals that were still
    -- pending at that moment. That is the most common leakage in time-series
    -- backtesting and it flatters results exactly at the short horizons where a
    -- model is supposed to be strongest.
    CONSTRAINT bt_run_embargo_present CHECK (embargo_hours > 0)
);

CREATE TABLE IF NOT EXISTS backtest.bt_score (
    bt_score_id   bigserial   PRIMARY KEY,
    bt_run_id     uuid        NOT NULL REFERENCES backtest.bt_run (bt_run_id),
    model         text        NOT NULL,
    horizon_group text        NOT NULL,
    n             integer     NOT NULL,
    mae           numeric(10,4),
    rmse          numeric(10,4),
    bias          numeric(10,4),
    mase          numeric(10,4),

    -- NFR-9: no accuracy figure without its sample size. Enforcing it in the
    -- table means a row cannot exist without one.
    CONSTRAINT bt_score_has_sample CHECK (n > 0),
    CONSTRAINT bt_score_grain UNIQUE (bt_run_id, model, horizon_group)
);

CREATE INDEX IF NOT EXISTS bt_score_run_idx ON backtest.bt_score (bt_run_id);

COMMENT ON COLUMN backtest.bt_score.model IS
    'ESO_final denotes the ESO published forecast as stored, which is NOT horizon-matched. A GridCast loss against it is close to uninformative; a win is a strong result.';

GRANT USAGE ON SCHEMA backtest TO gridcast_app, gridcast_readonly;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA backtest TO gridcast_app;
GRANT SELECT ON ALL TABLES IN SCHEMA backtest TO gridcast_readonly;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA backtest TO gridcast_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA backtest GRANT SELECT ON TABLES TO gridcast_readonly;
