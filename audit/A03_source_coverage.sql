-- A03 — What period does each source actually cover?
--
-- This query exists because of a mistake, not in anticipation of one.
--
-- The D-3 weather-location decision at M4 was made on `lnd_om_vintage`
-- believing it held the full 2018-2026 history. It held 2018-05-09 to
-- 2021-12-19 — 44% — because the backfill had died on Neon's storage limit.
-- `landing.run_log` recorded the failure with its error class at the moment it
-- happened, and was never read. The correlation returned 61,105 observations
-- per location, which is a plausible-looking number, and nobody asked why it
-- was not 144,000.
--
-- A partial series does not announce itself. It produces confident, plausible
-- numbers over whatever it happens to contain, and the conclusions drawn from
-- it are wrong in ways that look exactly like being right.
--
-- So coverage is now something to be read before any analysis, not inferred
-- afterwards from a row count that seemed about right.

with sources as (
    select 'lnd_ci_intensity' as source, min(sp_start_utc) as first_utc,
           max(sp_start_utc) as last_utc, count(distinct sp_start_utc) as periods
      from landing.lnd_ci_intensity
    union all
    select 'lnd_ci_genmix', min(sp_start_utc), max(sp_start_utc), count(distinct sp_start_utc)
      from landing.lnd_ci_genmix
    union all
    select 'lnd_ci_regional', min(sp_start_utc), max(sp_start_utc), count(distinct sp_start_utc)
      from landing.lnd_ci_regional
    union all
    select 'lnd_ex_demand', min(sp_start_utc), max(sp_start_utc), count(distinct sp_start_utc)
      from landing.lnd_ex_demand
    union all
    select 'lnd_ex_price', min(sp_start_utc), max(sp_start_utc), count(distinct sp_start_utc)
      from landing.lnd_ex_price
    union all
    select 'lnd_om_vintage', min(hour_start_utc), max(hour_start_utc), count(distinct hour_start_utc)
      from landing.lnd_om_vintage
    union all
    select 'lnd_om_archive', min(hour_start_utc), max(hour_start_utc), count(distinct hour_start_utc)
      from landing.lnd_om_archive
    union all
    select 'lnd_om_forecast', min(hour_start_utc), max(hour_start_utc), count(distinct hour_start_utc)
      from landing.lnd_om_forecast
),

span as (
    select
        min(sp_start_utc) as history_start,
        max(sp_start_utc) filter (where sp_start_utc < now()) as history_end
    from marts.dim_settlement_period
)

select
    s.source,
    s.first_utc::date  as covers_from,
    s.last_utc::date   as covers_to,
    s.periods,
    -- Days covered against days of history available. Anything materially below
    -- 100 means an analysis over this source is an analysis over a subset, and
    -- the subset is unlikely to be a random one.
    case
        when s.last_utc is null then 0
        else round(
            100.0 * (s.last_utc::date - s.first_utc::date)
                  / nullif(sp.history_end::date - sp.history_start::date, 0), 1)
    end as pct_of_history_span
from sources s
cross join span sp
order by pct_of_history_span, s.source;
