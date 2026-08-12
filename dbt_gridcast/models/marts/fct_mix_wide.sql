{{ config(materialized='incremental', unique_key='sp_start_utc', incremental_strategy='delete+insert') }}

/*
    fct_mix_wide — the mix pivoted, one row per settlement period.

    This is the shape every consumer actually wants, so this is the shape that
    is stored. 140k rows against the long format's 1.26M, for the same
    information — see fct_generation_mix for why the materialisation was
    inverted.

    It cannot drift from the long model because it is derived from it on every
    build rather than maintained alongside it.
*/

with mix as (
    select * from {{ ref('fct_generation_mix') }}
    {% if is_incremental() %}
    where sp_start_utc >= (
        select coalesce(max(sp_start_utc), '1970-01-01'::timestamptz) from {{ this }}
    ) - interval '{{ var("lookback_days") }} days'
    {% endif %}
)

select
    sp_start_utc,
    max(perc) filter (where fuel = 'wind')    as wind_perc,
    max(perc) filter (where fuel = 'solar')   as solar_perc,
    max(perc) filter (where fuel = 'gas')     as gas_perc,
    max(perc) filter (where fuel = 'coal')    as coal_perc,
    max(perc) filter (where fuel = 'nuclear') as nuclear_perc,
    max(perc) filter (where fuel = 'imports') as imports_perc,
    sum(perc) filter (where is_low_carbon)    as low_carbon_perc,
    sum(perc) filter (where is_fossil)        as fossil_perc,
    sum(perc)                                 as total_perc,
    count(*)                                  as fuels_reported,
    max(knowable_at_utc)                      as knowable_at_utc
from mix
group by sp_start_utc
