{{ config(materialized='view') }}

/*
    mart_excluded_scores — what mart_live_accuracy leaves out, and why.

    An exclusion nobody can see is an edit. This model is the difference: the
    accuracy surface drops scores issued in a configuration the model was not
    built for, and this publishes exactly how many, over what window, for what
    stated reason. The two models read the same declaration in the
    degraded_windows macro, so they cannot come to disagree about the boundary.

    The register keeps every row. These forecasts were genuinely issued, before
    the outcome existed, and the seals cover them. They are excluded from a
    claim about a MODEL, not from the record of what was published.

    Empty is the healthy state, and an empty result here is not the same as
    this model being absent — which is why the accuracy payload carries the
    count rather than only listing rows when there are any.
*/

with degraded as (

    {{ degraded_windows() }}

),

scored as (

    select
        f.model_version,
        f.run_at_utc,
        f.target_sp_start_utc,
        d.from_utc,
        d.until_utc,
        d.reason
    from {{ source('register', 'reg_forecast_point') }} f
    join {{ source('register', 'reg_forecast_score') }} s
      on s.forecast_id = f.forecast_id
    join degraded d
      on d.model_version = f.model_version
     and f.run_at_utc >= d.from_utc
     and f.run_at_utc <  d.until_utc

)

select
    model_version,
    reason,
    count(*)                    as n_excluded,
    min(run_at_utc)             as first_issued,
    max(run_at_utc)             as last_issued,
    min(target_sp_start_utc)    as first_target,
    max(target_sp_start_utc)    as last_target
from scored
group by model_version, reason
