/*
    stg_ci_intensity — national carbon intensity, one row per settlement period.

    Grain: sp_start_utc.

    Two things are deliberately absent.

    `intensity.index` is not carried through. M2 findings B03 and B04 showed the
    ESO recalibrates its band thresholds as the grid decarbonises — the
    moderate/high boundary walked from 260 gCO2/kWh in 2018 to 170 in 2026, so
    the bands overlap and the label encodes the publication year as much as the
    intensity. A model using it would partly be learning what year it is, then be
    asked to predict a year it has never seen. Excluding it at staging means no
    downstream model can reach it by accident.

    There is no `is_matured` here either. Maturity depends on wall-clock time,
    which makes it a property of a fact rather than of a source row.
*/

with latest as (

    {{ latest_landing_row(source('landing', 'lnd_ci_intensity'), ['sp_start_utc']) }}

),

history as (

    {{ landing_history(source('landing', 'lnd_ci_intensity'), ['sp_start_utc']) }}

)

select
    latest.sp_start_utc,

    (latest.payload -> 'intensity' ->> 'actual')::int   as actual_gco2_kwh,
    (latest.payload -> 'intensity' ->> 'forecast')::int as eso_forecast_gco2_kwh,

    -- The leakage boundary. Whatever the publisher claims about its own timing,
    -- we could not have known a value before we held it.
    latest.fetched_at_utc     as knowable_at_utc,
    history.first_seen_at_utc,
    history.revision_count,

    -- True for rows loaded by backfill, where fetched_at_utc is the moment the
    -- backfill ran rather than the moment the value became available. Design
    -- 8.3 requires results built on these to be reported in a separate column
    -- from live ones, never pooled.
    (history.first_seen_at_utc > latest.sp_start_utc + interval '30 days')
        as knowable_is_reconstructed

from latest
join history using (sp_start_utc)
