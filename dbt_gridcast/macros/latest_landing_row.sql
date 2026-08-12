{#
    Resolve an append-only landing table to the current value per key.

    Every staging model goes through this macro rather than writing its own
    DISTINCT ON. That is deliberate, and it is the direct consequence of M2
    finding B02.

    The Carbon Intensity API returned settlement period 2021-04-19 19:00 twice
    within a single response, with conflicting values. Both rows were therefore
    written in one transaction and share a fetched_at_utc. Ordering only by
    fetched_at_utc DESC leaves no defined winner: Postgres may return either
    row, and may return a different one on a later build — so two identical
    warehouse builds could disagree about a published figure, breaking
    reproducibility (NFR-3) in a way no test comparing a build to itself would
    ever catch.

    landing_id is a bigserial: unique, monotonic, never tied. Ordering by it
    makes "latest" total rather than partial.

    Putting it in a macro means a new staging model cannot forget the tiebreak,
    which is the only reliable way to hold a rule that matters twice in 144,763
    rows.
#}

{% macro latest_landing_row(source_relation, key_columns) %}

    select distinct on ({{ key_columns | join(', ') }})
        {{ key_columns | join(', ') }},
        payload,
        payload_hash,
        fetched_at_utc,
        landing_id,
        run_id
    from {{ source_relation }}
    order by
        {{ key_columns | join(', ') }},
        fetched_at_utc desc,
        landing_id desc

{% endmacro %}


{#
    How many distinct versions of a key the upstream has published, and when we
    first held any of them. Both are attributes of the revision history rather
    than of the current value, so they are computed separately and joined.
#}
{% macro landing_history(source_relation, key_columns) %}

    select
        {{ key_columns | join(', ') }},
        count(*)                        as revision_count,
        min(fetched_at_utc)             as first_seen_at_utc,
        count(distinct payload_hash)    as distinct_payloads
    from {{ source_relation }}
    group by {{ key_columns | join(', ') }}

{% endmacro %}
