/*
    A score from a degraded configuration must not reach the accuracy surface.

    The exclusion in mart_live_accuracy is a WHERE clause inside a view, and a
    WHERE clause is exactly what a later edit drops without noticing. The
    aggregate would still build, still return four models, and still look
    entirely reasonable — while measuring a model that ran for three days
    without its weather features, pooled with one that did not.

    The property is simple enough to state exactly. Every degraded point was
    issued before the window closed, so its target is earlier than the close
    too; every valid point was issued at or after it, so its target is later.
    A published row for a degraded model whose earliest target predates the
    window close is therefore counting points that should have been excluded.
*/

select
    p.model_version || ' publishes scores from before ' || d.until_utc as violation
from {{ ref('mart_live_accuracy') }} p
join ({{ degraded_windows() }}) d
  on d.model_version = p.model_version
where p.first_target < d.until_utc
