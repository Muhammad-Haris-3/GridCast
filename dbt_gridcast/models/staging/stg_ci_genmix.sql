/*
    stg_ci_genmix — generation mix, unnested to one row per fuel.

    Grain: (sp_start_utc, fuel).

    Landing stores one row per settlement period with the fuel array intact; the
    unnesting happens here. That ordering matters: M2 finding C02 confirmed the
    nine fuel categories are stable across all nine years, but if one ever
    appeared or disappeared, a wide table of nine fixed columns would have
    absorbed the change silently. In long format it shows up as a row count and
    a failing test.
*/

with latest as (

    {{ latest_landing_row(source('landing', 'lnd_ci_genmix'), ['sp_start_utc']) }}

)

select
    latest.sp_start_utc,
    fuel.value ->> 'fuel'              as fuel,
    (fuel.value ->> 'perc')::numeric(5, 2) as perc,
    latest.fetched_at_utc              as knowable_at_utc,

    -- Biomass is counted as low carbon here because the ESO counts it that way
    -- and the intensity figures downstream are theirs. The classification is
    -- genuinely contested — combustion emits at the stack, and the accounting
    -- treats regrowth as offsetting it — so the methods document states the
    -- choice rather than leaving a reader to infer it from a boolean.
    (fuel.value ->> 'fuel') in ('wind', 'solar', 'hydro', 'nuclear', 'biomass')
        as is_low_carbon,
    (fuel.value ->> 'fuel') in ('gas', 'coal')
        as is_fossil

from latest,
     lateral jsonb_array_elements(latest.payload -> 'generationmix') as fuel
