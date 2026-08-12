/*
    stg_om_forecast — the forward weather forecast the live system holds.

    Legitimate as a feature: at issue time this is genuinely available, so a
    model using it is using information it will also have in production.
*/

{{ stage_weather('lnd_om_forecast') }}
