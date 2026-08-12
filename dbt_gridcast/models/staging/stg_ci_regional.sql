/*
    stg_ci_regional — regional intensity for the DNO regions.

    Grain: (sp_start_utc, region_id).

    This model carries a forecast and **no actual column at all** — not a
    nullable one. Verified against the live API on 2026-08-12: regional
    responses return intensity.forecast only.

    A nullable `actual_gco2_kwh` here would invite a future join that produces
    an all-null accuracy table, and an all-null accuracy table looks like a bug
    in the join rather than a property of the data. Omitting the column makes
    the constraint structural: nothing downstream can compute regional accuracy,
    because there is nothing to compute it from (SRS 6.4, NFR-9, R-6).
*/

with latest as (

    {{ latest_landing_row(source('landing', 'lnd_ci_regional'), ['sp_start_utc', 'region_id']) }}

)

select
    sp_start_utc,
    region_id,
    (payload ->> 'shortname')                        as region_shortname,
    (payload ->> 'dnoregion')                        as dno_region,
    (payload -> 'intensity' ->> 'forecast')::int     as forecast_gco2_kwh,
    fetched_at_utc                                   as knowable_at_utc,

    -- Carried on every row so a consumer cannot reach the number without also
    -- reaching the statement that it can never be validated.
    false                                            as is_scoreable

from latest
