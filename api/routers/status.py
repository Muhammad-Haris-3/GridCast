"""Health and status endpoints.

/health must answer without touching the database. A liveness probe that
depends on Postgres reports the database's health, not the service's, and
turns one slow query into an apparently dead application.

/v1/status does touch the database, and degrades rather than 500s when it is
unreachable — on a free tier, "the database is asleep" is an ordinary Tuesday,
and the frontend needs to say so rather than show a broken page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from gridcast.config import get_settings
from gridcast.db import fetch_all, fetch_one

router = APIRouter()


@router.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.env,
        "commit": settings.commit,
        "time_utc": datetime.now(UTC).isoformat(),
    }


@router.get("/v1/status", tags=["status"])
def status() -> dict[str, Any]:
    """Pipeline health: spine coverage, recent runs, and role configuration.

    At M0 the only populated surface is the spine. Ingestion arrives at M1 and
    the run list fills in then; an empty run list here is the honest answer,
    not an error.
    """
    settings = get_settings()

    payload: dict[str, Any] = {
        "env": settings.env,
        "commit": settings.commit,
        "milestone": "M0 — walking skeleton",
        "database": "unreachable",
        "readonly_role_in_use": settings.readonly_role_in_use,
        "warnings": [],
        "spine": None,
        "recent_runs": [],
    }

    if not settings.readonly_role_in_use:
        payload["warnings"].append(
            "Serving is using the pipeline database role. Configure "
            "GRIDCAST_READONLY_DATABASE_URL so the API cannot write."
        )

    try:
        spine = fetch_one(
            """
            SELECT count(*)          AS periods,
                   min(sp_start_utc) AS first_period_utc,
                   max(sp_start_utc) AS last_period_utc
              FROM marts.dim_settlement_period
            """,
            readonly=True,
        )
        runs = fetch_all(
            """
            SELECT source, job, status, started_at_utc, finished_at_utc,
                   rows_read, rows_written
              FROM landing.run_log
             ORDER BY started_at_utc DESC
             LIMIT 10
            """,
            readonly=True,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, do not 500
        payload["detail"] = type(exc).__name__
        return payload

    payload["database"] = "ok"
    payload["spine"] = spine
    payload["recent_runs"] = runs

    if not runs:
        payload["warnings"].append("No pipeline runs recorded yet — expected before M1.")

    return payload
