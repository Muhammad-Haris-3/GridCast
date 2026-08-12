{{ config(materialized='incremental', unique_key='demand_key', incremental_strategy='delete+insert') }}

/*
    fct_demand_period — demand outturn with vintage in the grain.

    Every published vintage is retained. fct_demand_current exposes the latest
    for descriptive use, named so that reaching for it inside a feature builder
    is an obvious error rather than a subtle one — a lineage test enforces that
    nothing feeding features depends on it.
*/

with demand as (
    select * from {{ ref('stg_ex_demand') }}
    {% if is_incremental() %}
    where sp_start_utc >= (
        select coalesce(max(sp_start_utc), '1970-01-01'::timestamptz) from {{ this }}
    ) - interval '{{ var("lookback_days") }} days'
    {% endif %}
)

select
    sp_start_utc::text || '|' || publish_time_utc::text as demand_key,
    sp_start_utc,
    publish_time_utc,
    demand_indo_mw,
    demand_itsdo_mw,
    settlement_date,
    settlement_period_no,
    knowable_at_utc,
    -- M2 D01 measured this at 30 minutes flat through p99, tailing to 312.
    round(extract(epoch from publish_time_utc - sp_start_utc) / 60.0) as publish_lag_minutes
from demand
