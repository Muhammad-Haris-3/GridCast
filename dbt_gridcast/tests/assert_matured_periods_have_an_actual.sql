/*
    A matured period must either have an actual or be flagged unscoreable.

    This is the test for the silent-freeze failure described in
    fct_intensity_period: an incremental model that never revisits periods which
    were null when first seen leaves them permanently missing, and every accuracy
    figure downstream is then computed over a quietly truncated set.

    Known-absent periods are excluded — those never existed upstream (M2 A02) and
    counting them here would make the pipeline look permanently broken for
    something it cannot fix.
*/

select
    sp_start_utc,
    actual_gco2_kwh,
    is_matured,
    is_permanently_unscoreable
from {{ ref('fct_intensity_period') }}
where is_matured
  and actual_gco2_kwh is null
  and not is_permanently_unscoreable
  and not is_known_absent_upstream
