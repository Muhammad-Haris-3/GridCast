/*
    Every fully covered calendar year must contain exactly two days that are
    not 48 periods long — one 46-period day in spring, one 50-period day in
    autumn.

    The companion to assert_periods_per_local_day, and the more useful of the
    two. That test catches a spine with no clock changes at all; this one
    catches an over-eager "fix" that applies the offset in the wrong direction,
    or applies it to every day, or drops the transition days entirely. Each of
    those produces a plausible-looking table that fails here.

    Partial years (2018, which starts in May, and the current year) are excluded
    by the coverage check rather than hard-coded, so the test keeps working in
    2027 without an edit.
*/

with day_counts as (

    select
        settlement_date_local,
        extract(year from settlement_date_local)::int as yr,
        count(*) as n_periods
    from {{ ref('dim_settlement_period') }}
    group by 1, 2

),

covered_years as (

    select yr
    from day_counts
    group by yr
    having min(settlement_date_local) = make_date(yr, 1, 1)
       and max(settlement_date_local) = make_date(yr, 12, 31)

)

select
    c.yr,
    count(*) filter (where d.n_periods <> 48) as non_48_day_count
from covered_years c
join day_counts d on d.yr = c.yr
group by c.yr
having count(*) filter (where d.n_periods <> 48) <> 2
