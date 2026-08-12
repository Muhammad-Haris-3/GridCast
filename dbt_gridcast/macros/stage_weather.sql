{#
    The three Open-Meteo landing tables share a shape, so they share a macro —
    but they must never share a model, because they mean three different things
    and conflating any two of them is leakage:

      archive   reanalysis actuals. What the weather WAS. Never a training
                feature: production will never have it.
      forecast  what is predicted now. What the live system genuinely holds.
      vintage   what was predicted at a past moment, as issued. The only honest
                source for training on history.

    A model trained on archive actuals learns to rely on perfect knowledge of
    future weather. It backtests beautifully and fails in production, and the
    gap stays invisible until the live scoreboard opens. A lineage test asserts
    that nothing feeding the feature builder depends on the archive model.
#}

{% macro stage_weather(landing_table) %}

with latest as (

    {{ latest_landing_row(
         source('landing', landing_table),
         ['location_id', 'hour_start_utc']
       ) }}

)

select
    location_id,
    hour_start_utc,

    (payload ->> 'temperature_2m')::numeric(6, 2)      as temperature_2m_c,
    (payload ->> 'wind_speed_100m')::numeric(6, 2)     as wind_speed_100m_kmh,
    (payload ->> 'shortwave_radiation')::numeric(8, 2) as shortwave_radiation_wm2,
    (payload ->> 'cloud_cover')::numeric(5, 2)         as cloud_cover_pct,

    fetched_at_utc as knowable_at_utc

from latest

{% endmacro %}
