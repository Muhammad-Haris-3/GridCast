{{ config(
    materialized='incremental',
    unique_key='sp_start_utc',
    incremental_strategy='delete+insert'
) }}

/*
    fct_intensity_period — one row per national settlement period.

    THE LOOKBACK IS THE WHOLE DESIGN.

    A naive incremental model filters on `sp_start_utc > max(sp_start_utc)`,
    appends new periods, and never revisits the ones that were null when first
    seen. Actuals arrive late — so those periods stay frozen as missing, for
    ever. The failure is silent, compounding, and would invalidate every accuracy
    figure downstream. It is the single most likely way this project could
    produce confidently wrong numbers, which is why the window is deliberate
    rather than incidental.

    `lookback_days` is a dbt variable, not a literal, so M2's measurement of the
    revision tail can change it without a code edit.
*/

with intensity as (

    select * from {{ ref('stg_ci_intensity') }}
    {% if is_incremental() %}
    where sp_start_utc >= (
        select coalesce(max(sp_start_utc), '1970-01-01'::timestamptz)
          from {{ this }}
    ) - interval '{{ var("lookback_days") }} days'
    {% endif %}

),

known_absent as (
    select sp_start_utc from {{ ref('mart_absent_periods') }}
)

select
    i.sp_start_utc,
    i.actual_gco2_kwh,
    i.eso_forecast_gco2_kwh,

    -- The ESO's own error, computed once here so nothing downstream can
    -- disagree about its sign. Positive means the ESO forecast too high.
    case
        when i.actual_gco2_kwh is not null and i.eso_forecast_gco2_kwh is not null
        then i.eso_forecast_gco2_kwh - i.actual_gco2_kwh
    end as eso_error_gco2_kwh,

    i.knowable_at_utc,
    i.first_seen_at_utc,
    i.revision_count,
    i.knowable_is_reconstructed,

    -- Maturity: old enough that the actual should have arrived, and not still
    -- moving.
    --
    -- The stability half is subtler than it first looks, and the original
    -- formulation was wrong. Measuring stability from first_seen_at_utc marked
    -- the entire backfilled history immature, because first_seen for a
    -- backfilled row is when the backfill ran — an hour ago — not when the
    -- value settled. Every one of 144,761 periods came back is_matured = false,
    -- which would have made the scoring job find nothing to score and
    -- assert_matured_periods_have_an_actual pass vacuously.
    --
    -- What the window is actually for is not scoring a value that is still
    -- being revised. If we have only ever seen one version there is nothing to
    -- wait for; if we have seen it change, the latest version must hold for
    -- stability_hours before it is trusted.
    (
        now() - i.sp_start_utc > interval '{{ var("maturity_hours") }} hours'
        and (
            i.revision_count = 1
            or now() - i.knowable_at_utc > interval '{{ var("stability_hours") }} hours'
        )
    ) as is_matured,

    -- Scoreability, and the two ways it fails (M2 finding B01).
    --
    -- A matured period with no actual is not pending. It is permanently
    -- unscoreable — 625 such periods exist, concentrated in 2019 — and the
    -- scoring job must retire it rather than wait for ever.
    (
        i.actual_gco2_kwh is null
        and now() - i.sp_start_utc > interval '{{ var("maturity_hours") }} hours'
    ) as is_permanently_unscoreable,

    -- A period with no ESO forecast cannot appear in the institutional
    -- comparison. FR-20 requires every model scored on identical periods, so
    -- this must exclude the period for ALL models — otherwise GridCast is
    -- credited with periods its benchmark never had the chance to attempt.
    (i.eso_forecast_gco2_kwh is null) as eso_benchmark_missing,

    (i.actual_gco2_kwh is not null and i.eso_forecast_gco2_kwh is not null)
        as is_comparable,

    (a.sp_start_utc is not null) as is_known_absent_upstream

from intensity i
left join known_absent a on a.sp_start_utc = i.sp_start_utc
