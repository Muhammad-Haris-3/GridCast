-- B01 — Periods that can never be scored
--
-- Two separate failures, both fatal to scoring and neither visible as a gap,
-- because the row exists.
--
--   * actual IS NULL   — no ground truth. The forecast can never be graded.
--   * forecast IS NULL — no ESO benchmark. The period cannot appear in the
--                        institutional comparison.
--
-- The second is the subtle one. SRS FR-20 requires every model to be scored on
-- *identical* periods. A period where the ESO forecast is absent must therefore
-- be excluded from the comparison set for **all** models, not just for the ESO
-- — otherwise GridCast would be credited with periods its benchmark never had
-- the chance to attempt, which is precisely the kind of quiet advantage this
-- project exists not to take.
--
-- Finding (2026-08-12), periods older than 7 days:
--   625 with a null actual, concentrated in 2019 (308, 1.76%). None since 2024.
--    43 with a null ESO forecast, in exactly two blocks:
--         2025-01-12 23:00 -> 2025-01-13 11:30   (26 periods)
--         2025-08-10 23:00 -> 2025-08-11 07:00   (17 periods)
--
-- Consequence for M5: a period older than the maturity threshold whose actual
-- is still null is permanently unscoreable, not pending. The scoring job must
-- retire it rather than wait forever.

WITH current AS (
    SELECT DISTINCT ON (sp_start_utc)
           sp_start_utc,
           payload
    FROM landing.lnd_ci_intensity
    ORDER BY sp_start_utc, fetched_at_utc DESC, landing_id DESC
)
SELECT
    date_part('year', sp_start_utc)::int AS year,
    count(*)                                                                    AS periods,
    count(*) FILTER (WHERE payload->'intensity'->>'actual'   IS NULL)           AS null_actual,
    round(100.0 * count(*) FILTER (WHERE payload->'intensity'->>'actual' IS NULL)
          / count(*), 3)                                                        AS pct_null_actual,
    count(*) FILTER (WHERE payload->'intensity'->>'forecast' IS NULL)           AS null_eso_forecast
FROM current
-- Older than the ingestion horizon, so genuinely pending periods are excluded
-- and what remains is permanent absence rather than normal publication lag.
WHERE sp_start_utc < now() - interval '7 days'
GROUP BY 1
ORDER BY 1;
