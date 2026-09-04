{{ config(materialized='view') }}

/*
    fct_weather_period — VINTAGE weather aligned to settlement periods.

    What was predicted at a past moment, as issued. This is the model TRAINING
    reads, and the only honest source for it: it reproduces the quality of
    information a forecast would actually have had at that time.

    It cannot serve issuing, and that is not a limitation to work around. The
    vintage endpoint is backward-looking by construction — gridcast.sources.
    open_meteo.fetch_vintage clamps its window to the past — so this model holds
    no row for any hour that has not happened yet, which is precisely the set of
    hours a live forecast needs. Issuing reads fct_weather_period_live.

    That distinction went unmade for three weeks and cost the challenger. G2
    issued against this model, found nothing at its targets, and produced
    forecasts with every forward weather feature NaN — silently, because
    HistGradientBoosting accepts NaN without complaint. Then the serving weather
    window narrowed to three days, this model stopped advancing for want of a
    vintage ingest, the frame came back empty, and G2 stopped issuing at all.
    The visible failure and the invisible one had the same cause.

    The alignment to half-hourly periods lives in the weather_periods macro,
    which this model and the live one both call, so a model trained here and
    served there is looking at the same variable.

    MATERIALISED AS A VIEW, deliberately, and for the third time in this project
    the same reasoning applies. The source is hourly; this model is half-hourly,
    so storing it duplicates every weather observation into two rows purely for
    join convenience — 380,448 rows and 23 MB against 190,224 hourly source rows.

    Neon's 512 MB ceiling has already stopped this project twice: it killed the
    om_vintage backfill at 44% and failed the M3 build outright. Storing a shape
    that is only ever consumed in another shape is the habit that cost 115 MB on
    the generation mix, and it was about to cost another 50 MB here as the
    weather history completes.

    Reads the materialised hourly table, not staging. That indirection is what
    allows lnd_om_vintage to be pruned: nothing else touches the raw payloads,
    so once they are typed into fct_weather_hour the 184 MB of JSON behind them
    is dead weight.
*/

{{ weather_periods(ref('fct_weather_hour')) }}
