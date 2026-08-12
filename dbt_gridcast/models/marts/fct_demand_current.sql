{{ config(materialized='view') }}

/*
    fct_demand_current — the latest vintage per settlement period.

    DESCRIPTIVE USE ONLY. Using this inside a feature builder would mean asking
    what demand turned out to be rather than what was believed at issue time,
    which is leakage. The name is deliberately explicit and a lineage test backs
    it up, because a rule that depends on remembering is not a rule.
*/

select distinct on (sp_start_utc)
    sp_start_utc,
    publish_time_utc,
    demand_indo_mw,
    demand_itsdo_mw,
    settlement_date,
    settlement_period_no
from {{ ref('fct_demand_period') }}
order by sp_start_utc, publish_time_utc desc
