/*
    Every settlement period must report all nine fuel categories.

    M2 finding C02 confirmed the set is stable across all nine years. This is
    the test that would catch it changing — a category appearing or disappearing
    upstream would otherwise alter the row count of every model built on the mix
    without anything failing.
*/

select
    sp_start_utc,
    count(*) as fuels_reported
from {{ ref('fct_generation_mix') }}
group by sp_start_utc
having count(*) <> 9
