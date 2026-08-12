/*
    Generation-mix percentages must sum to 100 within +/- 0.5 points.

    The tolerance is measured, not guessed (M2 finding C01). Across 140,398
    periods the observed range is 99.7 to 100.3, with 62% landing exactly on
    100.0 — cumulative rounding across nine one-decimal figures, not error.

    +/- 0.5 is loose enough never to fire on that rounding and tight enough that
    a fuel category with more than half a percentage point of share disappearing
    upstream would breach it.

    Stated limitation: a fuel below 0.5% share vanishing would not trip this.
    assert_fuel_set_is_stable covers that case instead.
*/

select
    sp_start_utc,
    total_perc
from {{ ref('fct_mix_wide') }}
where total_perc is not null
  and abs(total_perc - 100) > 0.5
