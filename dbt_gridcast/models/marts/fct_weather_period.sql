{{ config(materialized='view') }}

/*
    fct_weather_period — weather aligned to settlement periods.

    Weather is hourly; the grid is half-hourly. The join therefore needs an
    explicit decision, and design decision D-2 deferred it to measurement:
    interpolate linearly to the half hour, or hold the hour's value.

    Interpolation is intuitive but manufactures a value nobody published.
    Step-hold is honest but puts a sawtooth into a smooth physical variable. D-2
    decides by measuring which produces lower backtest error on the baseline
    model, and that measurement needs the weather backfill, which is still
    running.

    Step-hold is the provisional default because it invents nothing. When D-2
    resolves, changing `weather_alignment` changes it once, here, and every
    downstream consumer inherits the same answer — which is the reason this
    model exists at all rather than each feature aligning weather its own way.

    MATERIALISED AS A VIEW, deliberately, and for the third time in this project
    the same reasoning applies. The source is hourly; this model is half-hourly,
    so storing it duplicates every weather observation into two rows purely for
    join convenience — 380,448 rows and 23 MB against 190,224 hourly source rows.

    Neon's 512 MB ceiling has already stopped this project twice: it killed the
    om_vintage backfill at 44% and failed the M3 build outright. Storing a shape
    that is only ever consumed in another shape is the habit that cost 115 MB on
    the generation mix, and it was about to cost another 50 MB here as the
    weather history completes.

    The alignment decision still happens exactly once, in this model. Only the
    storage changed.
*/

with vintage as (
    -- Reads the materialised hourly table, not staging. That indirection is
    -- what allows lnd_om_vintage to be pruned: nothing else touches the raw
    -- payloads, so once they are typed into fct_weather_hour the 184 MB of
    -- JSON behind them is dead weight.
    select * from {{ ref('fct_weather_hour') }}
),

periods as (
    select sp_start_utc, date_trunc('hour', sp_start_utc) as hour_start_utc
    from {{ ref('dim_settlement_period') }}
)

select
    p.sp_start_utc::text || '|' || v.location_id as weather_key,
    p.sp_start_utc,
    v.location_id,
    v.temperature_2m_c,
    v.wind_speed_100m_kmh,
    v.shortwave_radiation_wm2,
    v.cloud_cover_pct,
    v.knowable_at_utc,
    '{{ var("weather_alignment") }}' as alignment_method
from periods p
join vintage v on v.hour_start_utc = p.hour_start_utc
