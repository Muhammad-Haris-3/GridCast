-- GridCast — 007 Interval calibration
-- Idempotent.
--
-- The published intervals come from the model's own historical error
-- distribution: for each horizon band, the spread of the seasonal-naive error
-- over a year of matured actuals. That is 17,520 half-hourly observations
-- reduced to sixteen numbers.
--
-- Issuing recomputed it every run. Sixteen numbers calibrated on a year of data
-- do not move perceptibly in thirty minutes, so 48 times a day the pipeline
-- shipped a year of history out of the database to arrive at very nearly the
-- previous answer. On a plan metered in bytes read, that single query was the
-- largest recurring cost in the project, and it contributed to the outage of
-- 2026-08-17 that stopped the register growing.
--
-- The window stays a year — it is a modelling choice, and narrowing it would
-- change every published interval. The FREQUENCY is what was wrong. The
-- calibration is now computed once a day and read from here.
--
-- Append-only, like the rest of the register, and for a stronger reason than
-- symmetry: these numbers set the uncertainty attached to every forecast the
-- project publishes. If they could be edited, an interval could be widened
-- after the fact to cover an outcome that fell outside it. Keeping every
-- calibration ever used also makes the intervals of any past forecast
-- reconstructable — the set in force when it was issued is still here.

CREATE TABLE IF NOT EXISTS register.reg_error_quantile (
    calibration_id     bigserial   PRIMARY KEY,

    -- Every row written by one calibration run shares this. Grouping on the
    -- timestamp instead would be a bug waiting for two runs in the same second,
    -- and a half-applied set would silently mix two calibrations.
    calibration_run_id uuid        NOT NULL,
    computed_at_utc    timestamptz NOT NULL DEFAULT now(),

    -- The horizon band this offset applies to, in settlement periods.
    band_low           integer     NOT NULL,
    band_high          integer     NOT NULL,

    -- 'q025' | 'q10' | 'q90' | 'q975' — the same names the forecast rows carry.
    quantile_name      text        NOT NULL,

    -- Added to the point forecast to produce the quantile. Signed: the low
    -- quantiles are negative offsets and the high ones positive, and storing
    -- them signed keeps the arithmetic at issue time a plain addition.
    offset_gco2_kwh    double precision NOT NULL,

    -- What the estimate rests on. Published beside the intervals, because a
    -- quantile fitted on 400 samples and one fitted on 17,000 are different
    -- claims and the difference should not need to be inferred.
    n_samples          integer     NOT NULL,

    -- Days of history the calibration read. NULL means the full archive.
    source_days        integer,

    computed_by_commit text        NOT NULL,

    CONSTRAINT error_quantile_band_ordered CHECK (band_high >= band_low),
    CONSTRAINT error_quantile_named CHECK (quantile_name IN ('q025', 'q10', 'q90', 'q975')),
    CONSTRAINT error_quantile_sampled CHECK (n_samples > 0),

    -- One offset per band and quantile within a run. Without this a retried
    -- insert could double a set, and the reader would pick between duplicates
    -- arbitrarily.
    CONSTRAINT error_quantile_unique UNIQUE (calibration_run_id, band_low, band_high, quantile_name)
);

COMMENT ON TABLE register.reg_error_quantile IS
    'Interval calibration in force, one row per horizon band and quantile. Computed daily from a year of matured actuals; read by every issuing run.';
COMMENT ON COLUMN register.reg_error_quantile.calibration_run_id IS
    'Groups the rows of one calibration. A reader must take a whole set or none of it — mixing two calibrations would produce intervals that never existed.';

-- The only access pattern: the newest complete set, every issuing run.
CREATE INDEX IF NOT EXISTS reg_error_quantile_recent_idx
    ON register.reg_error_quantile (computed_at_utc DESC);

GRANT SELECT, INSERT ON register.reg_error_quantile TO gridcast_app;
GRANT SELECT ON register.reg_error_quantile TO gridcast_readonly;
GRANT USAGE, SELECT ON SEQUENCE register.reg_error_quantile_calibration_id_seq TO gridcast_app;

REVOKE UPDATE, DELETE, TRUNCATE ON register.reg_error_quantile FROM gridcast_app;
