{{ config(
    materialized='incremental',
    unique_key='weather_hour_key',
    incremental_strategy='delete+insert'
) }}

/*
    fct_weather_hour — typed weather at its natural hourly grain.

    Exists to let the raw JSON behind it be pruned.

    INCREMENTAL, AND THAT IS NOT OPTIONAL.

    This was first written as `materialized='table'`, which destroyed 434,592
    rows of weather history the moment it ran after the landing prune. A table
    model is rebuilt from its source on every `dbt build`; once the source held
    only the 7-day retention window, so did the model. The rows survived being
    pruned and were then deleted by the thing that was supposed to preserve them.

    The pattern only works if the materialised copy accumulates independently of
    the source it was extracted from. fct_mix_wide and fct_demand_period came
    through the same prune untouched, because both are incremental.

    Any model that exists so its source can be discarded must be incremental.
    A full-refresh of this model after a prune is unrecoverable except by
    re-fetching from the upstream API.

    `lnd_om_vintage` holds 425,000 payloads at 184 MB — 39% of the entire
    database and by far its largest object — to carry four numbers per row. The
    same information typed is roughly 50 bytes rather than 433, because a jsonb
    payload stores its keys on every single row.

    Nothing reads the raw form except this model. Once it is materialised, the
    landing table can be pruned to a short window for revision capture, and the
    warehouse keeps eight years of weather it can actually afford.

    This is the same trade made at M3 for the generation mix and at M6 for the
    weather period: stop paying to store a shape nothing consumes.
*/

with latest as (

    {{ latest_landing_row(
         source('landing', 'lnd_om_vintage'),
         ['location_id', 'hour_start_utc']
       ) }}

)

select
    location_id || '|' || hour_start_utc::text as weather_hour_key,
    location_id,
    hour_start_utc,
    (payload ->> 'temperature_2m')::numeric(6, 2)      as temperature_2m_c,
    (payload ->> 'wind_speed_100m')::numeric(6, 2)     as wind_speed_100m_kmh,
    (payload ->> 'shortwave_radiation')::numeric(8, 2) as shortwave_radiation_wm2,
    (payload ->> 'cloud_cover')::numeric(5, 2)         as cloud_cover_pct,
    fetched_at_utc                                     as knowable_at_utc

from latest
