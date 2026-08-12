/*
    stg_ex_price — market index price.

    Grain: (sp_start_utc, data_provider).

    Provider stays in the grain rather than being averaged away. The two
    providers do not agree: N2EXMIDP frequently reports a zero price on zero
    volume in the same period where APXMIDP reports a real trade. Averaging them
    at staging would bake that artefact into every downstream cost figure with
    no way to recover the components.
*/

with latest as (

    {{ latest_landing_row(source('landing', 'lnd_ex_price'), ['sp_start_utc', 'data_provider']) }}

)

select
    sp_start_utc,
    data_provider,
    (payload ->> 'price')::numeric(10, 2)  as price_gbp_mwh,
    (payload ->> 'volume')::numeric(14, 3) as volume_mwh,
    fetched_at_utc                          as knowable_at_utc,

    -- A zero-volume quote is not a market price, it is the absence of one.
    -- Flagged rather than filtered, because which providers go quiet when is
    -- itself worth being able to query.
    ((payload ->> 'volume')::numeric = 0)   as is_zero_volume

from latest
