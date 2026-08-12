-- B02 — Periods the upstream reported more than one way
--
-- This is the query that found the most consequential defect in M2, and the
-- defect was in GridCast's planned staging rule rather than in the data.
--
-- The design specifies that staging resolves landing to a current value with
--
--     SELECT DISTINCT ON (sp_start_utc) ...
--     ORDER BY sp_start_utc, fetched_at_utc DESC
--
-- Finding (2026-08-12): two periods carry conflicting versions, and one of them
-- has **both versions under a single fetched_at_utc**:
--
--   2021-04-19 19:00   actual 303 / forecast 294   |  actual 295 / forecast 289
--                      both fetched in the same transaction, because the API
--                      returned the period twice within one response
--
--   2019-12-17 23:30   identical actual and forecast, but index "moderate" in
--                      one response and "high" in another five seconds later
--
-- With a tied fetched_at_utc, DISTINCT ON has no defined winner. Postgres may
-- return either row, and may return a different one on a later build. Two
-- otherwise identical warehouse builds could therefore disagree about a
-- published figure — which breaks reproducibility (NFR-3) silently, in a way no
-- test comparing a build to itself would ever catch.
--
-- Resolution: every DISTINCT ON in this project orders by
--     fetched_at_utc DESC, landing_id DESC
-- landing_id is a bigserial: unique, monotonic, and never tied. The tiebreak
-- costs nothing and makes "latest" total rather than partial.
--
-- Two rows in 144,763 is not a large problem. An unreproducible warehouse is.

SELECT
    sp_start_utc,
    count(*)                          AS versions,
    count(DISTINCT fetched_at_utc)    AS distinct_fetch_times,
    count(DISTINCT payload_hash)      AS distinct_payloads,
    -- The dangerous case: more versions than fetch times means the ordering
    -- key is tied and the winner is arbitrary.
    (count(*) > count(DISTINCT fetched_at_utc)) AS ordering_is_ambiguous,
    array_agg(payload->'intensity'->>'actual' ORDER BY landing_id)   AS actuals,
    array_agg(payload->'intensity'->>'forecast' ORDER BY landing_id) AS forecasts
FROM landing.lnd_ci_intensity
GROUP BY sp_start_utc
HAVING count(*) > 1
ORDER BY sp_start_utc;
