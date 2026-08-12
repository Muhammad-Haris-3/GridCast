{{ config(materialized='view') }}

/*
    fct_generation_mix — one row per fuel per settlement period.

    Long format remains the source of truth: every test about the mix runs here,
    and a fuel category appearing or disappearing upstream shows up as a row
    count rather than being absorbed by a fixed set of columns.

    MATERIALISED AS A VIEW, deliberately. As a table it held 1,263,582 rows and
    cost 115 MB — the single largest object in the warehouse, on a 512 MB Neon
    project that the M3 build exhausted outright:

        could not extend file because project size limit (512 MB) has been exceeded

    Nothing reads the long format except tests and the wide model. Paying 115 MB
    to store a shape that is always consumed in another shape is the most
    expensive habit in the warehouse, so the materialisation is inverted: the
    long model computes on demand, and fct_mix_wide — 140k rows rather than
    1.26M — is the table.

    This changes which model is stored. It does not change which is authoritative.
*/

select
    sp_start_utc::text || '|' || fuel as mix_key,
    sp_start_utc,
    fuel,
    perc,
    is_low_carbon,
    is_fossil,
    knowable_at_utc
from {{ ref('stg_ci_genmix') }}
