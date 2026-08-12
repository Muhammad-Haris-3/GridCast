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


def _diagnose(exc: Exception) -> str:
    """Turn a database connection failure into the action that fixes it.

    "OperationalError" tells an operator nothing. A rejected password, an
    unreachable host and a missing role need three completely different
    responses, and the person reading this page is usually the one who can fix
    it in about a minute — if they are told which of the three it is.

    The raw driver message is classified rather than echoed. It contains the
    host, port and role, and while it does not contain the password, this
    endpoint is public and unauthenticated: matching on known causes cannot
    leak, whereas passing text through can.
    """
    text = str(exc).lower()

    if "password authentication failed" in text or "authentication" in text:
        return (
            "The database rejected the credential. GRIDCAST_READONLY_DATABASE_URL "
            "is wrong or stale — most likely the password was rotated and the "
            "serving environment still holds the old one."
        )
    if "does not exist" in text:
        return (
            "The role or database named in GRIDCAST_READONLY_DATABASE_URL does not "
            "exist on this server. Check the URL points at the right Neon project."
        )
    if "timeout" in text or "timed out" in text:
        return (
            "The database did not answer in time. If this is the first request in "
            "a while the instance may be waking; reload once before investigating."
        )
    if "could not translate host" in text or "name or service not known" in text:
        return "The database hostname could not be resolved. Check the host in the URL."
    if "ssl" in text:
        return "TLS negotiation failed. Neon requires sslmode=require in the URL."
    return "The database could not be reached, and the cause was not recognised."


@router.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.env,
        "commit": settings.build_id,
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
        "commit": settings.build_id,
        "milestone": "M5 — live forecasting loop",
        "serving_host": settings.serving_host,
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

    if not settings.env_is_valid:
        payload["warnings"].append(
            "GRIDCAST_ENV does not look like an environment label and has been "
            "suppressed. If a connection string was pasted into it, treat that "
            "credential as exposed and rotate it."
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
        payload["diagnosis"] = _diagnose(exc)
        payload["warnings"].append(payload["diagnosis"])
        return payload

    payload["database"] = "ok"
    payload["spine"] = spine
    payload["recent_runs"] = runs

    if not runs:
        payload["warnings"].append("No pipeline runs recorded yet — expected before M1.")

    return payload
