-- GridCast — 002 Landing layer and run log
-- Idempotent.
--
-- Every landing table has the same shape (design doc section 4.2):
--   natural key | payload jsonb | payload_hash | fetched_at_utc | run_id
--
-- fetched_at_utc is the leakage boundary. Whatever a publisher claims about its
-- own timing, we could not have known a value before we held it.

-- ---------------------------------------------------------------------------
-- Run log (FR-5)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS landing.run_log (
    run_log_id      bigserial PRIMARY KEY,
    run_id          uuid        NOT NULL,
    source          text        NOT NULL,
    job             text        NOT NULL,
    window_from_utc timestamptz,
    window_to_utc   timestamptz,
    started_at_utc  timestamptz NOT NULL DEFAULT now(),
    finished_at_utc timestamptz,
    http_calls      integer     NOT NULL DEFAULT 0,
    rows_read       integer     NOT NULL DEFAULT 0,
    rows_written    integer     NOT NULL DEFAULT 0,
    status          text        NOT NULL DEFAULT 'running',
    error_class     text,
    error_detail    text,
    CONSTRAINT run_log_status_valid
        CHECK (status IN ('running', 'success', 'partial', 'failed'))
);

CREATE INDEX IF NOT EXISTS run_log_run_id_idx  ON landing.run_log (run_id);
CREATE INDEX IF NOT EXISTS run_log_started_idx ON landing.run_log (started_at_utc DESC);
CREATE INDEX IF NOT EXISTS run_log_source_idx  ON landing.run_log (source, started_at_utc DESC);

COMMENT ON COLUMN landing.run_log.rows_read IS
    'Records returned by the API.';
COMMENT ON COLUMN landing.run_log.rows_written IS
    'Records actually inserted. rows_written < rows_read is the normal healthy state: it is insert-if-changed working. rows_written suddenly equalling rows_read across a long window means every payload changed, which is a monitored signal of upstream mass revision or a schema change.';

-- ---------------------------------------------------------------------------
-- Carbon Intensity API — national intensity
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS landing.lnd_ci_intensity (
    landing_id     bigserial   PRIMARY KEY,
    sp_start_utc   timestamptz NOT NULL,
    payload        jsonb       NOT NULL,
    payload_hash   bytea       NOT NULL,
    fetched_at_utc timestamptz NOT NULL DEFAULT now(),
    run_id         uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_ci_intensity_key_idx
    ON landing.lnd_ci_intensity (sp_start_utc, fetched_at_utc DESC);

-- ---------------------------------------------------------------------------
-- Carbon Intensity API — national generation mix
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS landing.lnd_ci_genmix (
    landing_id     bigserial   PRIMARY KEY,
    sp_start_utc   timestamptz NOT NULL,
    payload        jsonb       NOT NULL,
    payload_hash   bytea       NOT NULL,
    fetched_at_utc timestamptz NOT NULL DEFAULT now(),
    run_id         uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_ci_genmix_key_idx
    ON landing.lnd_ci_genmix (sp_start_utc, fetched_at_utc DESC);

-- ---------------------------------------------------------------------------
-- Carbon Intensity API — regional intensity
--
-- Verified 2026-08-12: regional responses carry intensity.forecast ONLY.
-- There is no actual. Regional intensity can never be scored (SRS 6.4, R-6).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS landing.lnd_ci_regional (
    landing_id     bigserial   PRIMARY KEY,
    sp_start_utc   timestamptz NOT NULL,
    region_id      smallint    NOT NULL,
    payload        jsonb       NOT NULL,
    payload_hash   bytea       NOT NULL,
    fetched_at_utc timestamptz NOT NULL DEFAULT now(),
    run_id         uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_ci_regional_key_idx
    ON landing.lnd_ci_regional (sp_start_utc, region_id, fetched_at_utc DESC);

-- ---------------------------------------------------------------------------
-- Elexon BMRS — demand outturn
--
-- publish_time_utc is promoted out of the payload into a column because it is
-- part of the grain downstream: point-in-time features need to ask what we
-- believed demand was at a past instant, and the latest revision cannot answer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS landing.lnd_ex_demand (
    landing_id       bigserial   PRIMARY KEY,
    sp_start_utc     timestamptz NOT NULL,
    publish_time_utc timestamptz NOT NULL,
    payload          jsonb       NOT NULL,
    payload_hash     bytea       NOT NULL,
    fetched_at_utc   timestamptz NOT NULL DEFAULT now(),
    run_id           uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_ex_demand_key_idx
    ON landing.lnd_ex_demand (sp_start_utc, publish_time_utc DESC, fetched_at_utc DESC);

-- ---------------------------------------------------------------------------
-- Elexon BMRS — market index price
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS landing.lnd_ex_price (
    landing_id     bigserial   PRIMARY KEY,
    sp_start_utc   timestamptz NOT NULL,
    data_provider  text        NOT NULL,
    payload        jsonb       NOT NULL,
    payload_hash   bytea       NOT NULL,
    fetched_at_utc timestamptz NOT NULL DEFAULT now(),
    run_id         uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_ex_price_key_idx
    ON landing.lnd_ex_price (sp_start_utc, data_provider, fetched_at_utc DESC);

-- ---------------------------------------------------------------------------
-- Open-Meteo — weather
--
-- Three tables, not one, because they mean different things:
--   archive  = reanalysis actuals. NEVER a training feature (design doc 5.5).
--   forecast = forward forecasts, vintage in the key.
--   vintage  = historical forecasts as issued, for training on backfilled history.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS landing.lnd_om_archive (
    landing_id     bigserial   PRIMARY KEY,
    location_id    text        NOT NULL,
    hour_start_utc timestamptz NOT NULL,
    payload        jsonb       NOT NULL,
    payload_hash   bytea       NOT NULL,
    fetched_at_utc timestamptz NOT NULL DEFAULT now(),
    run_id         uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_om_archive_key_idx
    ON landing.lnd_om_archive (location_id, hour_start_utc, fetched_at_utc DESC);

CREATE TABLE IF NOT EXISTS landing.lnd_om_forecast (
    landing_id     bigserial   PRIMARY KEY,
    location_id    text        NOT NULL,
    hour_start_utc timestamptz NOT NULL,
    issued_at_utc  timestamptz NOT NULL,
    payload        jsonb       NOT NULL,
    payload_hash   bytea       NOT NULL,
    fetched_at_utc timestamptz NOT NULL DEFAULT now(),
    run_id         uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_om_forecast_key_idx
    ON landing.lnd_om_forecast (location_id, hour_start_utc, issued_at_utc DESC);

CREATE TABLE IF NOT EXISTS landing.lnd_om_vintage (
    landing_id     bigserial   PRIMARY KEY,
    location_id    text        NOT NULL,
    hour_start_utc timestamptz NOT NULL,
    issued_at_utc  timestamptz NOT NULL,
    payload        jsonb       NOT NULL,
    payload_hash   bytea       NOT NULL,
    fetched_at_utc timestamptz NOT NULL DEFAULT now(),
    run_id         uuid        NOT NULL
);

CREATE INDEX IF NOT EXISTS lnd_om_vintage_key_idx
    ON landing.lnd_om_vintage (location_id, hour_start_utc, issued_at_utc DESC);
