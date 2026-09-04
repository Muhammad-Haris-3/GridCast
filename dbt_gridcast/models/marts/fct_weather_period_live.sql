{{ config(materialized='view') }}

/*
    fct_weather_period_live — the forward weather forecast, aligned to periods.

    What Open-Meteo predicts NOW, for the next three days. This is what the live
    system genuinely holds at issue time, and it is the only weather source that
    covers a target period before that period happens. ISSUING READS THIS.

    Legitimate as a feature, and the reasoning is the same one that bars the
    archive: a weather forecast for tomorrow is information a production system
    really has today, whereas reanalysis actuals for tomorrow are not. The test
    is not whether a value describes the future — it is whether we could have
    held it at issue time.

    TRAINING MUST NEVER READ THIS. Not because the rows are dishonest, but
    because their timing is wrong for history: lnd_om_forecast holds, for a past
    hour, whatever was predicted shortly before that hour arrived. Training an
    origin 48 hours out against a value forecast one hour out would hand the
    model weather far better than it will ever have in production — the exact
    backtest-well-fail-live failure the vintage source exists to prevent.
    gridcast.features keeps the two apart by loading them through two named
    functions rather than one function with a flag, and tests/test_serving_
    weather.py asserts which reads which.

    A VIEW over staging rather than a materialised table, unlike its vintage
    twin. Nothing needs to accumulate here: lnd_om_forecast is a rolling window
    of the next 72 hours, every row of which is superseded within days, and the
    history worth keeping is already kept as vintage. Materialising it would
    store the same hours twice.

    IT DEPENDS ON RETENTION. Issuing reads three days behind the anchor for the
    wind ramp, so scripts/prune_landing.py must keep at least that much of
    lnd_om_forecast; tests/test_retention_safety.py holds the two numbers
    together.
*/

{{ weather_periods(ref('stg_om_forecast')) }}
