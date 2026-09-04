{#
    Windows in which a model issued in a configuration it was not built for.

    A forecast in the register is evidence of what was issued, and the register
    cannot be edited. That is the point of it. But a SCORE is a claim about a
    model, and a score of a model running without its inputs is a claim about
    something that no longer exists.

    G2 is the case this was written for. Between 2026-08-12 and 2026-09-04 the
    issuing path loaded weather from the vintage relation, which is
    backward-looking by construction and holds no row for any period being
    forecast. Every forward weather feature was NaN, and HistGradientBoosting
    accepts NaN silently, so the model issued and scored as though nothing were
    wrong. It scored MASE 0.81 at H1 against a backtest of 0.46-0.55, and the
    difference is not the model.

    Left in the published figures those points do two kinds of damage. Read
    alone they understate the challenger. Read after 2026-09-04 they are worse
    than that: mart_live_accuracy groups by model and horizon over the whole
    register, so within a day of G2 resuming they would have been silently
    POOLED with valid points, and the resulting number would describe neither
    configuration. This project already refuses to pool backtest and live
    results for exactly that reason.

    So they are excluded from the accuracy surface and reported separately by
    mart_excluded_scores, which is what keeps this an exclusion rather than a
    quiet edit. Nothing is removed from the register.

    The boundary is the first run that issued with live forecast weather,
    2026-09-04 15:50:51Z, rounded down to the minute. G2 did not issue at all
    between 2026-08-15 and that run, so any instant in the three-week gap would
    select the same rows; the precise one is used because a boundary that only
    works by luck is not a boundary.

    A macro because two models need the same list and must not disagree about
    it. A VALUES list because there is one entry: when there is a second, this
    becomes a seed and the argument for that will be obvious.
#}

{% macro degraded_windows() %}

select *
from (
    values (
        -- Cast explicitly. An untyped literal in a VALUES list arrives as
        -- `unknown`, and Postgres has nothing to infer from when it is then
        -- compared to a column in a NOT EXISTS on the other side of a CTE. The
        -- accuracy route carries the same note for the same reason.
        'G2_gbm_v1'::text,
        timestamptz '2026-08-12 00:00:00+00',
        timestamptz '2026-09-04 15:50:00+00',
        'issued with no weather features: serving read the vintage weather relation, which holds no row for a period being forecast'::text
    )
) as w (model_version, from_utc, until_utc, reason)

{% endmacro %}
