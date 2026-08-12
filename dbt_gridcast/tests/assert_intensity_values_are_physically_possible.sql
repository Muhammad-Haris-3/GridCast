/*
    Intensity values must be physically possible.

    The design specified a range test on `actual` (5.1) and it was never
    implemented. This adds it — and extends it to the forecast, which is where
    the corruption actually turned out to be.

    16 periods in 2018-2019 carry ESO forecasts up to 13,579 gCO2/kWh against a
    highest-ever actual of 447 and an all-coal ceiling near 900. One error of
    13,275 contributes more to a squared-error metric than ten thousand ordinary
    ones, and it inflated the benchmark's RMSE to nine times its MAE.

    This test does not fail on those rows: they are real, they are upstream, and
    hiding them would be dishonest. It fails on values outside a range so wide
    that nothing legitimate could reach it — the flag is_eso_forecast_plausible
    carries the routine exclusion, and this catches a new class of corruption
    that the flag was not designed for.
*/

select
    sp_start_utc,
    actual_gco2_kwh,
    eso_forecast_gco2_kwh
from {{ ref('fct_intensity_period') }}
where actual_gco2_kwh < 0
   or actual_gco2_kwh > 2000
   or eso_forecast_gco2_kwh < 0
   or eso_forecast_gco2_kwh > 20000
