{{ config(materialized='incremental', unique_key='weather_key', incremental_strategy='delete+insert') }}

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
*/

with vintage as (
    select * from {{ ref('stg_om_vintage') }}
    {% if is_incremental() %}
    -- max(sp_start_utc), not max(hour_start_utc).
    --
    -- hour_start_utc is not a column of this model, so a subquery selecting it
    -- from {{ this }} resolves against the outer query instead and Postgres
    -- rejects an aggregate in WHERE. The first build passed because the filter
    -- is only emitted when is_incremental() is true — so the model built clean
    -- once and failed on every run after, which is a failure that arrives at
    -- 04:00 in a scheduled pipeline rather than in development.
    where hour_start_utc >= (
        select coalesce(max(sp_start_utc), '1970-01-01'::timestamptz) from {{ this }}
    ) - interval '{{ var("lookback_days") }} days'
    {% endif %}
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
