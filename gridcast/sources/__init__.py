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
DAILY: tuple[str, ...] = (
    "ci_regional",
    "ex_price",
    "om_archive",
)

__all__ = ["DAILY", "REGISTRY", "SCHEDULED", "Record", "SourceSpec"]
