-- A01 — Settlement period coverage against the spine, by year
--
-- The spine says which periods should exist; landing says which do. Anything
-- the spine has and landing does not is either an ingestion failure or an
-- upstream absence, and A02 decides which.
--
-- Finding (2026-08-12): 179 periods missing across 2021, 2023 and 2024.
-- All are upstream. See A02.

WITH landed AS (
    SELECT DISTINCT sp_start_utc FROM landing.lnd_ci_intensity
)
SELECT
    d.year,
    count(*)                                        AS spine_periods,
    count(l.sp_start_utc)                           AS landed_periods,
    count(*) - count(l.sp_start_utc)                AS missing,
    round(100.0 * count(l.sp_start_utc) / count(*), 3) AS pct_covered
FROM marts.dim_settlement_period d
LEFT JOIN landed l ON l.sp_start_utc = d.sp_start_utc
WHERE d.sp_start_utc < now()
  AND d.sp_start_utc >= timestamptz '2018-05-09 00:00+00'
GROUP BY d.year
ORDER BY d.year;
