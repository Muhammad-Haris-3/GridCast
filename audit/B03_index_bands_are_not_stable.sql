-- B03 — Is `intensity.index` a function of the intensity value?
--
-- The Carbon Intensity API returns a categorical band alongside every value:
-- "very low", "low", "moderate", "high", "very high". It is tempting to use it
-- as a ready-made label for classification, or as a feature.
--
-- It is neither, and this query is why.
--
-- Finding (2026-08-12): the bands overlap enormously.
--
--   very low      0 - 79
--   low          25 - 179
--   moderate     90 - 279
--   high        170 - 379
--   very high   230 - 447
--
-- If the band were a function of the value the ranges would be disjoint. They
-- overlap because the ESO recalibrates the thresholds as the grid decarbonises:
-- 200 gCO2/kWh was "moderate" in 2019 and is "high" now. The same number means
-- different things in different years.
--
-- Consequences, both mandatory:
--   * `index` must never be a feature. It encodes the publication year as much
--     as the intensity, so a model using it would partly be learning what year
--     it is — and would then be asked to predict a year it has never seen.
--   * `index` must never be a target. A classifier trained on it would be
--     fitting a moving definition and its accuracy would drift with the bands
--     rather than with the grid.
--
-- If a banded presentation is wanted, GridCast derives its own thresholds and
-- states them. Borrowing a definition that silently changes underneath is how
-- a chart ends up showing a trend that is entirely an artefact of relabelling.

WITH current AS (
    SELECT DISTINCT ON (sp_start_utc)
           sp_start_utc,
           (payload->'intensity'->>'actual')::int AS actual_gco2_kwh,
           payload->'intensity'->>'index'         AS index_band
    FROM landing.lnd_ci_intensity
    ORDER BY sp_start_utc, fetched_at_utc DESC, landing_id DESC
)
SELECT
    index_band,
    count(*)                 AS periods,
    min(actual_gco2_kwh)     AS min_actual,
    max(actual_gco2_kwh)     AS max_actual,
    min(date_part('year', sp_start_utc))::int AS first_year,
    max(date_part('year', sp_start_utc))::int AS last_year
FROM current
WHERE actual_gco2_kwh IS NOT NULL
GROUP BY index_band
ORDER BY min_actual;
