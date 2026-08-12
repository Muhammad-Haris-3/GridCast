-- GridCast — 003 The forecast register
-- Idempotent.
--
-- This is the evidential core of the project. Everything here exists to make
-- one claim checkable by someone who does not trust us: that no forecast was
-- edited after its outcome became known.

-- ---------------------------------------------------------------------------
-- reg_forecast_point — every forecast ever issued
--
-- Grain: (model_version, run_at_utc, target_sp_start_utc)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS register.reg_forecast_point (
    forecast_id           uuid        PRIMARY KEY,
    model_version         text        NOT NULL,
    run_id                uuid        NOT NULL,
    run_at_utc            timestamptz NOT NULL,
    target_sp_start_utc   timestamptz NOT NULL,
    horizon_periods       smallint    NOT NULL,
    point_gco2_kwh        numeric(8,3) NOT NULL,
    q025_gco2_kwh         numeric(8,3),
    q10_gco2_kwh          numeric(8,3),
    q90_gco2_kwh          numeric(8,3),
    q975_gco2_kwh         numeric(8,3),
    code_commit           text        NOT NULL,
    feature_snapshot_hash bytea       NOT NULL,
    row_hash              bytea       NOT NULL,
    created_at_utc        timestamptz NOT NULL DEFAULT now(),

    -- A forecast must be about the future. Enforced by the database, not by
    -- application code, because application code can be edited by whoever is
    -- motivated to edit the forecast.
    CONSTRAINT forecast_is_forward
        CHECK (target_sp_start_utc > run_at_utc),

    CONSTRAINT horizon_in_range
        CHECK (horizon_periods BETWEEN 1 AND 96),

    -- Quantiles must be ordered. A crossed quantile is a modelling bug that
    -- would otherwise surface as an interval that renders inside-out.
    CONSTRAINT quantiles_ordered
        CHECK (
            (q025_gco2_kwh IS NULL AND q10_gco2_kwh IS NULL
             AND q90_gco2_kwh IS NULL AND q975_gco2_kwh IS NULL)
            OR (q025_gco2_kwh <= q10_gco2_kwh
                AND q10_gco2_kwh <= q90_gco2_kwh
                AND q90_gco2_kwh <= q975_gco2_kwh)
        ),

    CONSTRAINT forecast_grain_unique
        UNIQUE (model_version, run_at_utc, target_sp_start_utc)
);

CREATE INDEX IF NOT EXISTS reg_forecast_target_idx
    ON register.reg_forecast_point (target_sp_start_utc, model_version);
CREATE INDEX IF NOT EXISTS reg_forecast_run_idx
    ON register.reg_forecast_point (run_at_utc DESC, model_version);
-- Sealing partitions the register by UTC month. The expression casts to a naive
-- timestamp first because date_trunc(text, timestamptz) is only STABLE — it
-- depends on the session TimeZone setting — and PostgreSQL will not index a
-- non-immutable expression. Casting pins the month boundary to UTC, which is
-- also the behaviour the seal needs: a month whose boundary moved with the
-- server's timezone would produce a different hash on a different machine.
CREATE INDEX IF NOT EXISTS reg_forecast_month_idx
    ON register.reg_forecast_point (date_trunc('month', run_at_utc AT TIME ZONE 'UTC'));

COMMENT ON TABLE register.reg_forecast_point IS
    'APPEND-ONLY. UPDATE and DELETE are revoked from the application role in 004_roles.sql. A forecast is evidence of what was believed at a moment in time.';
COMMENT ON COLUMN register.reg_forecast_point.feature_snapshot_hash IS
    'sha256 of the exact feature vector used. Makes a disputed forecast resolvable: the inputs can be recomputed from the warehouse vintage history and compared.';
COMMENT ON COLUMN register.reg_forecast_point.row_hash IS
    'sha256 over the forecast content. The unit of the monthly integrity seal.';

-- ---------------------------------------------------------------------------
-- reg_forecast_score — scoring, kept SEPARATE
--
-- Scoring is a separate table joined by forecast_id, never a column added to
-- the register. Adding an actual column to the register would mean the register
-- gets written twice, which would mean it is not append-only, which would mean
-- the seal is meaningless.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS register.reg_forecast_score (
    forecast_id               uuid        PRIMARY KEY
        REFERENCES register.reg_forecast_point (forecast_id),
    scored_at_utc             timestamptz NOT NULL DEFAULT now(),
    actual_gco2_kwh           integer     NOT NULL,
    abs_error                 numeric(8,3) NOT NULL,
    sq_error                  numeric(12,3) NOT NULL,
    pinball_10                numeric(8,3),
    pinball_90                numeric(8,3),
    in_80_interval            boolean,
    in_95_interval            boolean,
    scale_mae_seasonal_naive  numeric(8,3) NOT NULL,
    scoring_commit            text        NOT NULL
);

CREATE INDEX IF NOT EXISTS reg_score_scored_idx
    ON register.reg_forecast_score (scored_at_utc DESC);

COMMENT ON COLUMN register.reg_forecast_score.scale_mae_seasonal_naive IS
    'The MASE denominator, computed on the training window and stored so the ratio remains reproducible years later even if the reference series is revised.';

-- ---------------------------------------------------------------------------
-- reg_forecast_seal — the integrity guarantee (FR-19)
--
-- One row per closed month. The same values are committed to seals/YYYY-MM.json
-- in git, which turns "trust my database" into "check my git history against my
-- live database" — verifiable by someone with access to neither.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS register.reg_forecast_seal (
    period_month     date        PRIMARY KEY,
    row_count        bigint      NOT NULL,
    seal_hash        bytea       NOT NULL,
    sealed_at_utc    timestamptz NOT NULL DEFAULT now(),
    sealed_by_commit text        NOT NULL
);

COMMENT ON TABLE register.reg_forecast_seal IS
    'seal_hash = sha256(string_agg(row_hash, order by forecast_id)) over the month. Recomputed daily and compared. Any mismatch fails the pipeline loudly.';

-- ---------------------------------------------------------------------------
-- reg_seal_audit — the audit trail of the audit
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS register.reg_seal_audit (
    audit_id        bigserial   PRIMARY KEY,
    checked_at_utc  timestamptz NOT NULL DEFAULT now(),
    period_month    date        NOT NULL,
    expected_hash   bytea       NOT NULL,
    observed_hash   bytea       NOT NULL,
    expected_count  bigint      NOT NULL,
    observed_count  bigint      NOT NULL,
    passed          boolean     NOT NULL
);

CREATE INDEX IF NOT EXISTS reg_seal_audit_idx
    ON register.reg_seal_audit (checked_at_utc DESC);

-- A failed audit must remain visible forever. Recording only the latest result
-- would let a failure be erased by the next successful check.
