/*
    stg_om_archive — reanalysis weather actuals.

    DESCRIPTIVE USE ONLY. This model must never reach the feature builder: it
    describes what the weather turned out to be, which a forecasting system in
    production will never know at issue time. A lineage test enforces that
    nothing feeding features depends on it.
*/

{{ stage_weather('lnd_om_archive') }}
