/*
    stg_om_vintage — past weather forecasts as they were issued.

    The leakage-safe source for training on history. It reproduces the quality
    of information a forecast would actually have had at the time, rather than
    the quality hindsight provides.
*/

{{ stage_weather('lnd_om_vintage') }}
