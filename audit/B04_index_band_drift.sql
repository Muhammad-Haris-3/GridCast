-- B04 — Confirming the band drift directly
--
-- B03 shows the bands overlap. This shows why: the threshold between bands
-- moves year on year. Taking the boundary between "moderate" and "high" as the
-- example, the highest value still called moderate and the lowest already
-- called high are computed per year.
--
-- A stable definition would produce a flat line. A recalibrated one produces a
-- staircase, and the staircase is the evidence that `index` carries the year
-- inside it.

WITH current AS (
    SELECT DISTINCT ON (sp_start_utc)
           sp_start_utc,
           (payload->'intensity'->>'actual')::int AS actual_gco2_kwh,
           payload->'intensity'->>'index'         AS index_band
    FROM landing.lnd_ci_intensity
    ORDER BY sp_start_utc, fetched_at_utc DESC, landing_id DESC
)
SELECT
    date_part('year', sp_start_utc)::int                                   AS year,
    max(actual_gco2_kwh) FILTER (WHERE index_band = 'moderate')            AS highest_moderate,
    min(actual_gco2_kwh) FILTER (WHERE index_band = 'high')                AS lowest_high,
    max(actual_gco2_kwh) FILTER (WHERE index_band = 'low')                 AS highest_low,
    min(actual_gco2_kwh) FILTER (WHERE index_band = 'moderate')            AS lowest_moderate
FROM current
WHERE actual_gco2_kwh IS NOT NULL
GROUP BY 1
ORDER BY 1;
