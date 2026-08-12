-- E01 — Which weather locations actually predict the grid? (design decision D-3)
--
-- PENDING: requires the lnd_om_vintage backfill to complete.
--
-- Six sample locations were committed in M1 so the backfill had something to
-- run against, chosen on geography rather than evidence: two Scottish onshore
-- wind points, two offshore, one demand centre, one southern solar point. The
-- design was explicit that they are provisional.
--
-- This query is how they stop being provisional. Each location's hub-height
-- wind speed is correlated against national wind share. A location that adds
-- nothing beyond another is a feature that costs storage, training time and a
-- degree of freedom, and buys noise.
--
-- The decision rule, fixed here before the numbers are seen so it cannot be
-- chosen to suit them:
--   * keep a location whose correlation with national wind share exceeds 0.5
--   * where two locations correlate above 0.9 with each other, keep the one
--     with the stronger relationship to wind share and drop the other
--   * keep at least one demand-centre location regardless of wind correlation,
--     because temperature drives demand rather than wind

WITH wind_share AS (
    SELECT DISTINCT ON (sp_start_utc)
           sp_start_utc,
           (SELECT (fuel->>'perc')::numeric
              FROM jsonb_array_elements(payload->'generationmix') AS fuel
             WHERE fuel->>'fuel' = 'wind') AS wind_perc
    FROM landing.lnd_ci_genmix
    ORDER BY sp_start_utc, fetched_at_utc DESC, landing_id DESC
),
weather AS (
    SELECT DISTINCT ON (location_id, hour_start_utc)
           location_id,
           hour_start_utc,
           (payload->>'wind_speed_100m')::numeric AS wind_speed_100m,
           (payload->>'temperature_2m')::numeric  AS temperature_2m
    FROM landing.lnd_om_vintage
    ORDER BY location_id, hour_start_utc, fetched_at_utc DESC, landing_id DESC
)
SELECT
    w.location_id,
    count(*)                                                   AS observations,
    round(corr(w.wind_speed_100m, s.wind_perc)::numeric, 4)    AS corr_wind_share,
    round(corr(w.temperature_2m, s.wind_perc)::numeric, 4)     AS corr_temp_wind_share,
    round(avg(w.wind_speed_100m)::numeric, 1)                  AS mean_wind_kmh
FROM weather w
JOIN wind_share s
  -- Hourly weather against half-hourly grid data: the hour is matched to both
  -- of its settlement periods. This is a join for correlation only. The
  -- half-hourly alignment decision (D-2) is made once, in fct_weather_period.
  ON s.sp_start_utc >= w.hour_start_utc
 AND s.sp_start_utc <  w.hour_start_utc + interval '1 hour'
WHERE s.wind_perc IS NOT NULL
GROUP BY w.location_id
ORDER BY corr_wind_share DESC NULLS LAST;
