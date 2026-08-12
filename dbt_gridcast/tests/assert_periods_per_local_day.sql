/*
    Every local settlement day must have exactly 46, 48 or 50 periods.

    This is the clock-change test. A spine that produces a tidy 48 every day of
    the year is not a correct spine — it is a spine built in UTC and mislabelled
    as local, and every "same period tomorrow" feature built on it will be
    silently wrong for two days a year.

    The first and last local dates are excluded: they are partial by
    construction, because the spine starts and ends at UTC instants that fall
    mid-day in Europe/London whenever BST is in effect.
*/

with day_counts as (

    select
        settlement_date_local,
        count(*) as n_periods
    from {{ ref('dim_settlement_period') }}
    group by 1

),

bounds as (

    select
        min(settlement_date_local) as first_date,
        max(settlement_date_local) as last_date
    from day_counts

)

select
    d.settlement_date_local,
    d.n_periods
from day_counts d
cross join bounds b
where d.settlement_date_local > b.first_date
  and d.settlement_date_local < b.last_date
  and d.n_periods not in (46, 48, 50)
