/*
    Weather reanalysis actuals must never reach a feature.

    stg_om_archive describes what the weather turned out to be. A forecasting
    system in production will never have that at issue time, so a model trained
    on it learns to rely on perfect knowledge of the future. It would backtest
    beautifully and fail in production, and the gap would stay invisible until
    the live scoreboard opened.

    TWO models feed the feature builder and both are checked here:

      fct_weather_period       the vintage source, which training reads
      fct_weather_period_live  the forward forecast, which issuing reads

    Either one repointed at the archive is the same leak, and the second is the
    easier mistake to make: it is the model that legitimately carries future
    hours, so an archive join there looks superficially reasonable.

    The dependency is asserted directly rather than trusted to a comment. A
    model that does not exist yet cannot violate anything, so each check is
    guarded on the relation being present — that is what lets this test run
    against a partially built warehouse without reporting a false violation.
*/

with checked as (
    select 'fct_weather_period'      as model
    union all
    select 'fct_weather_period_live' as model
),

reads_the_archive as (
    select distinct dependent.relname as model
    from pg_depend d
    join pg_rewrite r  on r.oid = d.objid
    join pg_class dependent  on dependent.oid = r.ev_class
    join pg_class referenced on referenced.oid = d.refobjid
    join pg_namespace n on n.oid = referenced.relnamespace
    where n.nspname = 'staging'
      and referenced.relname = 'stg_om_archive'
)

select c.model || ' must not read stg_om_archive' as violation
from checked c
join reads_the_archive a on a.model = c.model
