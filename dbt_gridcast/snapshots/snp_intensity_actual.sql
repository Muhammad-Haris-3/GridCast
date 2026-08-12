{% snapshot snp_intensity_actual %}

{{
    config(
        target_schema='marts',
        unique_key='sp_start_utc',
        strategy='check',
        check_cols=['actual_gco2_kwh', 'eso_forecast_gco2_kwh'],
        invalidate_hard_deletes=False
    )
}}

/*
    snp_intensity_actual — typed SCD2 history of published intensity values.

    The landing layer already records every revision: it is append-only and
    writes a row whenever a payload differs. This snapshot is a convenience over
    that record, not the record itself.

    It exists so the question "how much do actuals move after first publication,
    and for how long" is a query over valid_from / valid_to rather than a window
    function over JSON. That question is the input to D-1, which sets the
    maturity threshold, which decides when a forecast may be scored — so it is
    worth making cheap to ask.

    If this snapshot and the landing layer ever disagree, the landing layer is
    right.
*/

select
    sp_start_utc,
    actual_gco2_kwh,
    eso_forecast_gco2_kwh,
    revision_count,
    knowable_at_utc
from {{ ref('stg_ci_intensity') }}

{% endsnapshot %}
