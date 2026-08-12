-- A02 — Are the gaps ours or theirs?
--
-- The distinction decides the response. An ingestion gap is refetched. An
-- upstream absence must be recorded as permanent, or the daily deep-heal will
-- re-request the same five windows every night, forever, against a free API —
-- an impolite loop with no terminating condition.
--
-- Finding (2026-08-12): all five are upstream and permanent. Verified by
-- re-requesting each window directly: the API returns rows either side and
-- nothing within. The 2023 window jumps from 2023-10-20T21:30Z straight to
-- 2023-10-22T19:30Z.
--
--   2021-04-19 17:00              1 period
--   2021-04-19 22:00 - 22:30      2 periods
--   2021-12-26 15:00 - 12-27 17:30   54 periods (27.0 h)
--   2023-10-20 22:00 - 10-22 19:00   91 periods (45.5 h)
--   2024-06-11 23:00 - 06-12 14:00   31 periods (15.5 h)

WITH landed AS (
    SELECT DISTINCT sp_start_utc FROM landing.lnd_ci_intensity
),
missing AS (
    SELECT
        d.sp_start_utc,
        -- Consecutive periods share a constant when the row number is
        -- subtracted from the timestamp, which groups an outage into one row.
        d.sp_start_utc - (row_number() OVER (ORDER BY d.sp_start_utc)) * interval '30 minutes' AS island
    FROM marts.dim_settlement_period d
    LEFT JOIN landed l ON l.sp_start_utc = d.sp_start_utc
    WHERE d.sp_start_utc < now()
      AND d.sp_start_utc >= timestamptz '2018-05-09 00:00+00'
      AND l.sp_start_utc IS NULL
)
SELECT
    min(sp_start_utc)          AS gap_start_utc,
    max(sp_start_utc)          AS gap_end_utc,
    count(*)                   AS periods,
    round(count(*) / 2.0, 1)   AS hours
FROM missing
GROUP BY island
ORDER BY gap_start_utc;
