/*
    Consecutive spine rows must be exactly 30 minutes apart, in UTC, with no
    exceptions at clock changes.

    This is the test that distinguishes a correct implementation from a
    superficially correct one. In UTC there IS no clock change — the gap is
    always 30 minutes. The 46- and 50-period days are a property of the local
    calendar, not of the underlying time axis. If this test ever fails on a
    clock-change day, someone has applied a timezone conversion to the spine
    itself rather than to its derived local attributes.
*/

with gaps as (

    select
        sp_start_utc,
        sp_start_utc - lag(sp_start_utc) over (order by sp_start_utc) as step
    from {{ ref('dim_settlement_period') }}

)

select
    sp_start_utc,
    step
from gaps
where step is not null
  and step <> interval '30 minutes'
