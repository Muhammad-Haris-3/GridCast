"""Guards on the retention design.

Pruning landing data is only safe because a typed mart already holds what is
being discarded. That makes the marts load-bearing in a way ordinary derived
tables are not: they are no longer a convenience over the raw data, they *are*
the data.

A table-materialised model cannot play that role. dbt rebuilds it from source on
every run, so once the source holds only the retention window, so does the
model — and the rows survive being pruned only to be deleted by the thing meant
to preserve them.

That is not hypothetical. `fct_weather_hour` was written as a table, and the
first `dbt build` after the first prune destroyed 434,592 rows of weather
history. It was recoverable only because Open-Meteo still had it.

These tests read the model files rather than the database so they run anywhere,
including CI with an empty warehouse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MODELS = Path(__file__).resolve().parent.parent / "dbt_gridcast" / "models" / "marts"

# Models that exist so their landing source can be pruned. Each must accumulate
# independently of the source it was extracted from.
#
# Keep this in step with the targets in scripts/prune_landing.py: a landing
# table gains a retention window only once its mart is listed here.
MARTS_BACKING_A_PRUNE = [
    "fct_weather_hour",  # backs lnd_om_vintage
    "fct_mix_wide",  # backs lnd_ci_genmix
    "fct_demand_period",  # backs lnd_ex_demand
]


def model_sql(name: str) -> str:
    path = MODELS / f"{name}.sql"
    assert path.exists(), f"{name}.sql not found in {MODELS}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("model", MARTS_BACKING_A_PRUNE)
def test_models_backing_a_prune_are_incremental(model: str) -> None:
    """The rule that would have prevented the data loss.

    A model whose source gets pruned must be incremental. As a table it is
    rebuilt from a source that no longer contains the history, and the rebuild
    is silent — it succeeds, reports success, and leaves a fraction of the rows.
    """
    sql = model_sql(model)
    config = re.search(r"\{\{\s*config\((.*?)\)\s*\}\}", sql, re.DOTALL)
    assert config, f"{model} has no config block"

    assert "materialized='incremental'" in config.group(1).replace('"', "'"), (
        f"{model} backs a pruned landing table and must be incremental. "
        "As a table it is rebuilt from source on every dbt build, so once the "
        "source is pruned to its retention window the model is truncated to "
        "match — silently, and reporting success."
    )


@pytest.mark.parametrize("model", MARTS_BACKING_A_PRUNE)
def test_models_backing_a_prune_declare_a_unique_key(model: str) -> None:
    """Incremental without a unique key appends duplicates on every overlap."""
    config = re.search(r"\{\{\s*config\((.*?)\)\s*\}\}", model_sql(model), re.DOTALL).group(1)
    assert "unique_key" in config, f"{model} is incremental but declares no unique_key"


def test_prune_script_checks_a_prerequisite_mart_per_table() -> None:
    """Every pruned table must name the mart that already holds its content.

    A global prerequisite check is what deadlocked the first attempt: one unmet
    requirement blocked every table, including those whose marts had existed
    since M3 and which held the space needed to satisfy the requirement.
    """
    script = (Path(__file__).resolve().parent.parent / "scripts" / "prune_landing.py").read_text(
        encoding="utf-8"
    )
    assert "required_mart" in script, "the prune script must check prerequisites per table"
    assert "marts.fct_weather_hour" in script, "lnd_om_vintage must require fct_weather_hour"


def test_prune_script_does_not_use_vacuum_full() -> None:
    """VACUUM FULL cannot reclaim space on a database that is already full.

    It rewrites the table into fresh space before releasing the old, so freeing
    184 MB needs 184 MB free to do it in. The rebuild-and-drop approach needs
    room only for the rows being kept.
    """
    script = (Path(__file__).resolve().parent.parent / "scripts" / "prune_landing.py").read_text(
        encoding="utf-8"
    )
    # Look for the command being *executed*, not merely mentioned. The script's
    # own docstring explains at length why VACUUM FULL is unusable here, and a
    # naive substring search flags that explanation as the offence.
    executed = [
        line
        for line in script.splitlines()
        if "VACUUM" in line.upper() and ("execute" in line or "cur." in line)
    ]
    assert not executed, (
        "VACUUM FULL needs as much free space as the table it rewrites, which is "
        f"exactly what a database at its ceiling does not have. Found: {executed}"
    )
