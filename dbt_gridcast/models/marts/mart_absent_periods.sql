{{ config(materialized='table') }}

/*
    mart_absent_periods — settlement periods that will never exist.

    M2 finding A02 established that 179 missing periods across five windows are
    upstream and permanent, verified by re-requesting each window directly: the
    API returns rows either side and nothing within.

    Without this register the daily deep-heal re-requests all five windows every
    night, for ever, against a free public API with no terminating condition.
    That would be an impolite loop, and it would be GridCast's fault rather than
    the ESO's.

    Recording absence as data rather than as a code constant matters for a
    second reason: 99.88% is the true ceiling for historical coverage, and a
    coverage figure that silently counts unobtainable periods as failures would
    make the pipeline look permanently broken. NFR-1's 99% target is met against
    the achievable denominator, and this table is what defines it.

    A window is added here only after the absence has been verified at source.
    The alternative — treating any persistent gap as permanent — would let a
    genuine ingestion failure quietly become an accepted hole.
*/

with windows as (

    select
        window_from_utc::timestamptz as window_from_utc,
        window_to_utc::timestamptz   as window_to_utc,
        source,
        reason,
        verified_on::date            as verified_on
    from {{ ref('known_absent_windows') }}

)

select
    d.sp_start_utc,
    w.source,
    w.reason,
    w.verified_on

from windows w
join {{ ref('dim_settlement_period') }} d
  on d.sp_start_utc >= w.window_from_utc
 and d.sp_start_utc <  w.window_to_utc
