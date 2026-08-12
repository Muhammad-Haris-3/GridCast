-- GridCast — 006 Model registry
-- Idempotent.
--
-- In the `register` schema rather than `marts`, for the same reason the
-- forecast register lives there: promotion history is evidence, not a
-- derivation. A dbt model that could rebuild "which model was champion on
-- 3 March" would let the answer change, and the whole point of recording it is
-- that it cannot.
--
-- The design document places this in marts as dim_model_version. That was
-- wrong for the same reason it would have been wrong for the forecasts.

CREATE TABLE IF NOT EXISTS register.reg_model_version (
    model_version    text        PRIMARY KEY,
    model_family     text        NOT NULL,
    created_at_utc   timestamptz NOT NULL DEFAULT now(),
    code_commit      text        NOT NULL,

    -- What the model was fitted on. Null for models that fit nothing, which is
    -- most of the baselines — a distinction worth being able to query rather
    -- than inferring from the family name.
    train_from_utc   timestamptz,
    train_to_utc     timestamptz,
    feature_set      jsonb,
    hyperparameters  jsonb,

    -- 'champion'  — its forecasts are what the application serves
    -- 'challenger'— forecasts and is scored, but is never shown as the answer
    -- 'retired'   — no longer issuing
    role             text        NOT NULL DEFAULT 'challenger',
    role_since_utc   timestamptz NOT NULL DEFAULT now(),

    -- Set when a model uses the ESO published forecast as an input. Such a
    -- model may never appear in the same table as the ESO benchmark: it has not
    -- out-forecast the grid operator, it has bias-corrected them, and
    -- presenting that as "beating National Grid" would be the single most
    -- dishonest sentence available in this project (design 9.2).
    uses_eso_forecast boolean    NOT NULL DEFAULT false,

    notes            text,

    CONSTRAINT model_role_valid CHECK (role IN ('champion', 'challenger', 'retired'))
);

COMMENT ON TABLE register.reg_model_version IS
    'The model registry. Written by Python, read by dbt, rebuilt by nothing.';
COMMENT ON COLUMN register.reg_model_version.uses_eso_forecast IS
    'A model taking the ESO forecast as a feature is an ESO-augmented model and is reported on a separate surface. It has bias-corrected the benchmark, not beaten it.';

-- ---------------------------------------------------------------------------
-- Promotion history, append-only.
--
-- Every evaluation is recorded, whether or not it promotes. A registry holding
-- only successful promotions is evidence that failures went unrecorded, not
-- that none occurred (SRS FR-32, PREREGISTRATION 8).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS register.reg_promotion_event (
    promotion_id      bigserial   PRIMARY KEY,
    decided_at_utc    timestamptz NOT NULL DEFAULT now(),
    champion_version  text        NOT NULL REFERENCES register.reg_model_version (model_version),
    challenger_version text       NOT NULL REFERENCES register.reg_model_version (model_version),
    outcome           text        NOT NULL,
    n_per_group       jsonb       NOT NULL,
    test_statistics   jsonb       NOT NULL,
    preregistration_commit text   NOT NULL,
    notes             text,

    CONSTRAINT promotion_outcome_valid
        CHECK (outcome IN ('promoted', 'not_promoted', 'inconclusive', 'insufficient_sample'))
);

COMMENT ON COLUMN register.reg_promotion_event.preregistration_commit IS
    'The commit hash of PREREGISTRATION.md as it stood when the comparison began. A rule written after seeing the outcome is not a rule.';

CREATE INDEX IF NOT EXISTS reg_promotion_decided_idx
    ON register.reg_promotion_event (decided_at_utc DESC);

GRANT SELECT, INSERT, UPDATE ON register.reg_model_version TO gridcast_app;
GRANT SELECT, INSERT ON register.reg_promotion_event TO gridcast_app;
GRANT SELECT ON register.reg_model_version, register.reg_promotion_event TO gridcast_readonly;

-- reg_model_version permits UPDATE because `role` genuinely changes when a
-- model is promoted or retired. The promotion EVENTS that caused those changes
-- are append-only, so the history survives even though the current state moves.
REVOKE UPDATE, DELETE, TRUNCATE ON register.reg_promotion_event FROM gridcast_app;
REVOKE DELETE, TRUNCATE ON register.reg_model_version FROM gridcast_app;
