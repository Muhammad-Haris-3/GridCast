-- E01 — Which weather locations actually predict the grid? (design decision D-3)
--
-- Six sample locations were committed in M1 so the backfill had something to
-- run against, chosen on geography rather than evidence: two Scottish onshore
-- wind points, two offshore, one demand centre, one southern solar point. The
-- design was explicit that they were provisional.
--
-- This is how they stop being provisional. Each location's hub-height wind speed
-- is correlated against national wind share. A location that adds nothing beyond
-- another is a feature costing storage, training time and a degree of freedom,
-- and buying noise.
--
-- The decision rule, fixed before the numbers were seen so it could not be
-- chosen to suit them:
--   * keep a location whose correlation with national wind share exceeds 0.5
--   * where two locations correlate above 0.9 with each other, keep the one
--     with the stronger relationship to wind share and drop the other
--   * keep at least one demand-centre location regardless of wind correlation,
--     because temperature drives demand rather than wind
--
-- Rewritten at M4 to read the marts rather than staging. The original version
-- joined two jsonb-unnesting views over 300,000 landing rows and had not
-- returned after ten minutes on a free-tier instance. fct_weather_period and
-- fct_mix_wide are materialised and carry exactly the same information.

select
    w.location_id,
    count(*)                                                        as observations,
    round(corr(w.wind_speed_100m_kmh, m.wind_perc)::numeric, 4)     as corr_wind_share,
    round(corr(w.temperature_2m_c, m.wind_perc)::numeric, 4)        as corr_temp_wind_share,
    round(corr(w.shortwave_radiation_wm2, m.solar_perc)::numeric, 4) as corr_solar_share,
    round(avg(w.wind_speed_100m_kmh)::numeric, 1)                   as mean_wind_kmh
from marts.fct_weather_period w
join marts.fct_mix_wide m on m.sp_start_utc = w.sp_start_utc
where m.wind_perc is not null
group by w.location_id
order by corr_wind_share desc nulls last;
