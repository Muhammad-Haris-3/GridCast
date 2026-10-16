/*
    stg_ex_demand — demand outturn, with vintage in the grain.

    Grain: (sp_start_utc, publish_time_utc).

    This is the one staging model that does **not** resolve to a current value,
    and the exception is the whole point of the table. Point-in-time feature
    construction needs to ask what we believed demand was at a past instant, and
    the latest revision cannot answer that. Collapsing to current here would
    destroy the only information that makes the feature honest.

    M2 finding D01 measured publication at period end + 30 minutes at p50, p95
    and p99, tailing to 312 minutes, with exactly one vintage per period so far.
    The vintage stays in the grain regardless: INDO is explicitly an *initial*
    outturn, and later settlement runs are expected.

    The grain is enforced here, not assumed. On 2026-10-05 landing first held
    two rows for one (period, publish time), the unique test on
    fct_demand_period failed, and every run after it skipped issuing forecasts
    for twelve days. Landing can legitimately hold that: a payload changed
    without a new publishTime is stored as a revision. The LATEST fetch wins,
    with its own fetched_at as knowable_at, which can only make a value
    knowable later than it was, never earlier, so it cannot leak.
*/

select distinct on (sp_start_utc, publish_time_utc) *
from (
select
    (payload ->> 'startTime')::timestamptz   as sp_start_utc,
    (payload ->> 'publishTime')::timestamptz as publish_time_utc,

    (payload ->> 'initialDemandOutturn')::int                    as demand_indo_mw,
    (payload ->> 'initialTransmissionSystemDemandOutturn')::int  as demand_itsdo_mw,

    (payload ->> 'settlementDate')::date      as settlement_date,
    (payload ->> 'settlementPeriod')::smallint as settlement_period_no,

    fetched_at_utc as knowable_at_utc,
    landing_id

from {{ source('landing', 'lnd_ex_demand') }}
) landed
order by sp_start_utc, publish_time_utc, knowable_at_utc desc, landing_id desc
