-- C02 — Does the set of fuel categories change over eight years?
--
-- The mix is stored one row per settlement period with the fuel array intact,
-- and unnested downstream. That decision only holds up if the set of fuels is
-- stable: a category appearing or disappearing upstream would silently change
-- the row count of every model built on it, and a wide table with nine fixed
-- columns would have hidden the change entirely.
--
-- This is the query that would catch it.

WITH current AS (
    SELECT DISTINCT ON (sp_start_utc)
           sp_start_utc,
           payload
    FROM landing.lnd_ci_genmix
    ORDER BY sp_start_utc, fetched_at_utc DESC, landing_id DESC
),
fuels AS (
    SELECT
        date_part('year', sp_start_utc)::int AS year,
        fuel->>'fuel'                        AS fuel_name
    FROM current, jsonb_array_elements(payload->'generationmix') AS fuel
)
SELECT
    year,
    count(DISTINCT fuel_name)                          AS distinct_fuels,
    string_agg(DISTINCT fuel_name, ', ' ORDER BY fuel_name) AS fuel_set
FROM fuels
GROUP BY year
ORDER BY year;
