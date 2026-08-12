{{ config(materialized='table') }}

/*
    dim_settlement_period — the spine (design doc 2.3)

    Generated, not sourced. This is what makes absence detectable: a missing
    settlement period is a spine row with no fact, which is queryable. Without
    a spine, missing data is invisible — you cannot WHERE your way to rows that
    were never inserted.

    The primary key is sp_start_utc, never (settlement_date, settlement_period).
    The industry key is a trap: on clock-change days a settlement date has 46 or
    50 periods, and any arithmetic of the form "period + 48 = same time
    tomorrow" silently produces wrong answers twice a year.

    Local time is derived here for presentation and for analysing clock effects.
    It is never a join key.
*/

with spine as (

    select generate_series(
        timestamptz '{{ var("spine_start_utc") }}',
        date_trunc('day', now()) + interval '{{ var("spine_forward_days") }} days'
            - interval '30 minutes',
        interval '30 minutes'
    ) as sp_start_utc

),

localised as (

    select
        sp_start_utc,
        sp_start_utc + interval '30 minutes' as sp_end_utc,
        sp_start_utc at time zone 'Europe/London' as local_ts,
        (sp_start_utc at time zone 'Europe/London')
            - (sp_start_utc at time zone 'UTC') as utc_offset
    from spine

),

numbered as (

    select
        sp_start_utc,
        sp_end_utc,
        local_ts,
        utc_offset,
        local_ts::date as settlement_date_local,

        -- Period number within the LOCAL settlement day. Counting within the
        -- local day is what produces 46 and 50 correctly; counting within the
        -- UTC day would produce a tidy, wrong 48 every day of the year.
        row_number() over (
            partition by local_ts::date
            order by sp_start_utc
        )::smallint as settlement_period_no,

        count(*) over (
            partition by local_ts::date
        )::smallint as periods_in_local_day
    from localised

)

select
    sp_start_utc,
    sp_end_utc,
    settlement_date_local,
    settlement_period_no,
    periods_in_local_day,
    local_ts,
    extract(hour   from local_ts)::smallint as hour_local,
    extract(isodow from local_ts)::smallint as dow,          -- 1 = Monday
    extract(month  from local_ts)::smallint as month,
    extract(year   from local_ts)::smallint as year,
    extract(doy    from local_ts)::smallint as day_of_year,
    (extract(isodow from local_ts) >= 6) as is_weekend,
    (utc_offset = interval '1 hour') as is_bst,

    -- Placeholder until M2 resolves D-7 (holiday calendar source, and whether
    -- Scottish holidays materially affect demand). Deliberately false rather
    -- than null: a null here would silently poison every boolean feature built
    -- on it, whereas a wrong-but-known false is visible in the M2 comparison.
    false as is_gb_holiday,
    false as is_gb_holiday_resolved

from numbered
