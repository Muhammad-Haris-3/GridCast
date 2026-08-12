-- D01 — How long after a settlement period is its demand published? (D-1)
--
-- This is the one publication lag measurable from backfilled data, and it is
-- measurable only because Elexon states `publishTime` inside the payload.
--
-- Everything else must be observed forward. A backfilled row's fetched_at_utc
-- is the moment the backfill ran, not the moment the value became available, so
-- asking backfilled Carbon Intensity data when its actuals appeared would
-- return "all of them, today" — a confident and completely wrong answer. That
-- asymmetry is exactly why design doc 8.3 reports backtest and live results in
-- separate columns and never pools them.
--
-- The lag measured here sets:
--   * the embargo between training data and issue time in the backtest harness
--   * the reconstructed vintage offset applied to backfilled demand
--   * how long the scoring job waits before treating a period as mature

WITH vintages AS (
    SELECT
        sp_start_utc,
        (payload->>'publishTime')::timestamptz AS publish_time_utc,
        EXTRACT(epoch FROM (payload->>'publishTime')::timestamptz - sp_start_utc) / 60.0
            AS lag_minutes
    FROM landing.lnd_ex_demand
)
SELECT
    count(*)                                                            AS observations,
    count(DISTINCT sp_start_utc)                                        AS periods,
    round(min(lag_minutes))                                             AS min_lag_min,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY lag_minutes)::numeric)  AS p50_lag_min,
    round(percentile_cont(0.95) WITHIN GROUP (ORDER BY lag_minutes)::numeric)  AS p95_lag_min,
    round(percentile_cont(0.99) WITHIN GROUP (ORDER BY lag_minutes)::numeric)  AS p99_lag_min,
    round(max(lag_minutes))                                             AS max_lag_min,
    -- A publish time before the period it describes would mean the field has
    -- been misread, not that Elexon can see the future.
    count(*) FILTER (WHERE lag_minutes < 0)                             AS impossible_negative_lags
FROM vintages;
