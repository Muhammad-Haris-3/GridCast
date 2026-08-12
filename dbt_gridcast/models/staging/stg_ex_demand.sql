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
*/

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
