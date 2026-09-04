{{ config(materialized='view') }}

/*
    mart_live_accuracy — rolling out-of-sample accuracy from the live register.

    THIS IS WHERE FR-20's "IDENTICAL PERIODS" IS ENFORCED.

    A forecast point is only comparable if every model issuing at that run_at
    also forecast that target. Without the restriction, GridCast would be
    credited with periods the ESO never attempted — the ESO forecast reaches
    about 47 hours, so the final two horizons of every run have no benchmark —
    and its apparent lead would grow purely from scoring a different set.

    That is precisely the quiet advantage this project exists not to take, so it
    is enforced in the model rather than left to whoever writes the next query.

    Every row carries `n`. NFR-9 forbids publishing an accuracy figure without
    its sample size, and the cheapest way to comply is to make the number
    unobtainable without the count beside it.

    A VIEW, not a table: the register is small, the aggregate is cheap, and a
    stored copy could go stale against the evidence it claims to summarise.

    SCORES FROM A DEGRADED CONFIGURATION ARE EXCLUDED. A model that issued
    without the inputs it was built on is not the model this row claims to
    measure, and because the aggregate groups over the whole register those
    points would otherwise be pooled with valid ones the moment the model
    resumed. The windows are declared in the degraded_windows macro and the
    excluded points are published, with their count and the reason, by
    mart_excluded_scores. Nothing is removed from the register itself.
*/

with scored as (

    select
        f.forecast_id,
        f.model_version,
        f.run_at_utc,
        f.target_sp_start_utc,
        f.horizon_periods,
        s.actual_gco2_kwh,
        s.abs_error,
        s.sq_error,
        s.in_80_interval,
        s.in_95_interval,
        s.scale_mae_seasonal_naive,
        f.q90_gco2_kwh - f.q10_gco2_kwh as interval_width_80
    from {{ source('register', 'reg_forecast_point') }} f
    join {{ source('register', 'reg_forecast_score') }} s
      on s.forecast_id = f.forecast_id
    where not exists (
        select 1
        from ({{ degraded_windows() }}) d
        where d.model_version = f.model_version
          and f.run_at_utc >= d.from_utc
          and f.run_at_utc <  d.until_utc
    )

),

models_issuing as (

    -- How many distinct models forecast at each issue time. A target is
    -- comparable only when every one of them covered it.
    --
    -- The exclusion is repeated here and is not optional. Counting a degraded
    -- model as a participant would make every target it forecast uncomparable
    -- for everybody else, quietly deleting three days of valid B0, B1 and ESO
    -- points along with the invalid G2 ones.
    select run_at_utc, count(distinct model_version) as models_at_issue
    from {{ source('register', 'reg_forecast_point') }} f
    where not exists (
        select 1
        from ({{ degraded_windows() }}) d
        where d.model_version = f.model_version
          and f.run_at_utc >= d.from_utc
          and f.run_at_utc <  d.until_utc
    )
    group by run_at_utc

),

comparable as (

    select s.run_at_utc, s.target_sp_start_utc
    from scored s
    join models_issuing m on m.run_at_utc = s.run_at_utc
    group by s.run_at_utc, s.target_sp_start_utc, m.models_at_issue
    having count(distinct s.model_version) = max(m.models_at_issue)

),

horizon_grouped as (

    select
        s.*,
        case
            when s.horizon_periods between 1 and 6   then 'H1'
            when s.horizon_periods between 7 and 24  then 'H2'
            when s.horizon_periods between 25 and 48 then 'H3'
            else 'H4'
        end as horizon_group
    from scored s
    join comparable c
      on c.run_at_utc = s.run_at_utc
     and c.target_sp_start_utc = s.target_sp_start_utc

)

select
    model_version,
    horizon_group,
    count(*)                                        as n,
    round(avg(abs_error), 3)                        as mae,
    round(sqrt(avg(sq_error)), 3)                   as rmse,
    round(avg(abs_error) / nullif(max(scale_mae_seasonal_naive), 0), 4) as mase,
    round(avg(interval_width_80), 2)                as interval_width_80,
    round(avg(case when in_80_interval then 1.0 else 0.0 end), 4) as coverage_80,
    round(avg(case when in_95_interval then 1.0 else 0.0 end), 4) as coverage_95,
    min(target_sp_start_utc)                        as first_target,
    max(target_sp_start_utc)                        as last_target
from horizon_grouped
group by model_version, horizon_group
order by horizon_group, mae
