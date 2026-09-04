{#
    Align hourly weather to the half-hourly settlement period spine.

    Weather is hourly; the grid is half-hourly. The join therefore needs an
    explicit decision, and design decision D-2 deferred it to measurement:
    interpolate linearly to the half hour, or hold the hour's value.

    Interpolation is intuitive but manufactures a value nobody published.
    Step-hold is honest but puts a sawtooth into a smooth physical variable. D-2
    decides by measuring which produces lower backtest error on the baseline
    model. Step-hold is the provisional default because it invents nothing.

    IN A MACRO because two models now need it: the vintage one that training
    reads, and the live one that issuing reads. The alignment has to be
    identical in both. Two models aligning weather differently would put a model
    into production on a slightly different variable from the one it was trained
    on — the quietest way for a backtest to stop meaning anything, because
    nothing fails and every number still looks reasonable.

    So the alignment decision still happens exactly once. It just no longer
    happens inside one of the two models that has to obey it.
#}

{% macro weather_periods(hourly_relation) %}

with hourly as (
    select * from {{ hourly_relation }}
),

periods as (
    select sp_start_utc, date_trunc('hour', sp_start_utc) as hour_start_utc
    from {{ ref('dim_settlement_period') }}
)

select
    p.sp_start_utc::text || '|' || h.location_id as weather_key,
    p.sp_start_utc,
    h.location_id,
    h.temperature_2m_c,
    h.wind_speed_100m_kmh,
    h.shortwave_radiation_wm2,
    h.cloud_cover_pct,
    h.knowable_at_utc,
    '{{ var("weather_alignment") }}' as alignment_method
from periods p
join hourly h on h.hour_start_utc = p.hour_start_utc

{% endmacro %}
