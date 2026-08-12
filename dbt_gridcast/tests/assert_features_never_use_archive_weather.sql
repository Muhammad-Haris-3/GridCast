/*
    Weather reanalysis actuals must never reach a feature.

    stg_om_archive describes what the weather turned out to be. A forecasting
    system in production will never have that at issue time, so a model trained
    on it learns to rely on perfect knowledge of the future. It would backtest
    beautifully and fail in production, and the gap would stay invisible until
    the live scoreboard opened.

    fct_weather_period is the model features are built from, and it must draw
    from the vintage source only. This test asserts the dependency directly
    rather than trusting a comment: if somebody repoints it at the archive, the
    build fails.

    The same reasoning applies to fct_demand_current, which resolves demand to
    its latest revision rather than the vintage known at issue time.
*/

with feature_sources as (
    select
        view_definition
    from information_schema.views
    where table_schema = 'marts'
      and table_name in ('fct_mix_wide')

    union all

    select
        pg_get_viewdef(c.oid, true) as view_definition
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'marts'
      and c.relname in ('fct_mix_wide')
)

select 'fct_weather_period must not read stg_om_archive' as violation
where exists (
    select 1
    from information_schema.tables
    where table_schema = 'marts' and table_name = 'fct_weather_period'
)
and (
    select count(*)
    from pg_depend d
    join pg_rewrite r on r.oid = d.objid
    join pg_class dependent on dependent.oid = r.ev_class
    join pg_class referenced on referenced.oid = d.refobjid
    join pg_namespace n on n.oid = referenced.relnamespace
    where dependent.relname = 'fct_weather_period'
      and n.nspname = 'staging'
      and referenced.relname = 'stg_om_archive'
) > 0
