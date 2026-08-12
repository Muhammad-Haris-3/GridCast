{{ config(materialized='table') }}

/*
    dim_region — the DNO regions.

    is_scoreable is hard-coded false on every row and carried through to the API
    response, so the frontend cannot render a regional accuracy figure even by
    accident. Regional responses carry a forecast and no actual (SRS 6.4), so
    there is nothing to score against and there never will be.

    Enforcing that in the dimension rather than in a convention means a future
    model that joins here inherits the flag without anyone having to remember.
*/

select
    region_id,
    max(region_shortname) as region_shortname,
    max(dno_region)       as dno_region,
    false                 as is_scoreable,
    count(*)              as observed_periods
from {{ ref('stg_ci_regional') }}
group by region_id
