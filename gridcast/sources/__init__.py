"""The source registry.

Adding a source means writing one module and adding one line here. Nothing in
the ingestion machinery, the CLI or the workflows needs to know it exists.
"""

from __future__ import annotations

from gridcast.sources import carbon_intensity, elexon, open_meteo
from gridcast.sources.base import Record, SourceSpec

REGISTRY: dict[str, SourceSpec] = {
    spec.name: spec
    for spec in (
        carbon_intensity.INTENSITY,
        carbon_intensity.GENMIX,
        carbon_intensity.REGIONAL,
        elexon.DEMAND,
        elexon.PRICE,
        open_meteo.ARCHIVE,
        open_meteo.FORECAST,
        open_meteo.VINTAGE,
    )
}

# What the 30-minute pipeline run ingests. Deliberately not everything:
# the archive lags reality by days and the vintage endpoint only describes the
# past, so polling either every half hour would be pure waste on somebody
# else's free service.
SCHEDULED: tuple[str, ...] = (
    "ci_intensity",
    "ci_genmix",
    "ex_demand",
    "om_forecast",
)

# Heavier or slower-moving sources, run a few times a day.
#
# ci_regional and ex_price are DEFERRED to M8 and so are absent here. On the
# first live pipeline run they wrote 13,240 of 13,888 rows — 95% — for data no
# milestone before M8 consumes, and regional can never be scored at all.
DAILY: tuple[str, ...] = ("om_archive",)

# Sources switched off until the milestone that needs them. Excluded from
# ingestion AND from gap-fill: gap detection was the mechanism actually driving
# the writes, healing seven days of regional on every half-hourly run.
DEFERRED: tuple[str, ...] = tuple(
    sorted(name for name, spec in REGISTRY.items() if spec.deferred)
)

__all__ = ["DAILY", "DEFERRED", "REGISTRY", "SCHEDULED", "Record", "SourceSpec"]
