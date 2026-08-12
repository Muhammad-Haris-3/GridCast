-- C01 — Do generation-mix percentages sum to 100? (design decision D-6)
--
-- The design deferred the tolerance to measurement rather than picking a
-- plausible-looking epsilon. A tolerance set too tight fails on ordinary
-- rounding and teaches everyone to ignore the test; set too loose it would
-- accept a fuel category quietly vanishing from the response.
--
-- The distribution below is what the tolerance is chosen from.

WITH current AS (
    SELECT DISTINCT ON (sp_start_utc)
           sp_start_utc,
           payload
    FROM landing.lnd_ci_genmix
    ORDER BY sp_start_utc, fetched_at_utc DESC, landing_id DESC
),
sums AS (
    SELECT
        sp_start_utc,
        (SELECT sum((fuel->>'perc')::numeric)
           FROM jsonb_array_elements(payload->'generationmix') AS fuel) AS total_perc
    FROM current
)
SELECT
    round(total_perc, 1) AS mix_sum,
    count(*)             AS periods,
    round(100.0 * count(*) / sum(count(*)) OVER (), 4) AS pct_of_all
FROM sums
GROUP BY 1
ORDER BY 1;
