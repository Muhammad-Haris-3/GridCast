"""Health and status endpoints.

/health must answer without touching the database. A liveness probe that
depends on Postgres reports the database's health, not the service's, and
turns one slow query into an apparently dead application.

/v1/status does touch the database, and degrades rather than 500s when it is
unreachable — on a free tier, "the database is asleep" is an ordinary Tuesday,
and the frontend needs to say so rather than show a broken page.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from gridcast.config import get_settings
from gridcast.db import fetch_all, fetch_one

router = APIRouter()


def _redact(exc: Exception) -> str:
    """Strip everything identifying from a driver message, keep the sentence.

    Used only when no known cause matches. "The cause was not recognised" is a
    dead end: it tells the operator that the one page built to name the fault
    cannot name it, and leaves them with nothing to search for. Some of the
    message has to survive.

    What must not survive is anything that locates the deployment: hosts, IPs,
    ports, roles and quoted identifiers. This endpoint is public and
    unauthenticated. The password is not in the message, but the rest of the
    connection string effectively is, so it is removed rather than trusted.
    """
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<address>", str(exc))
    text = re.sub(r"\b[0-9a-f:]{4,}:[0-9a-f:]+\b", "<address>", text, flags=re.I)
    text = re.sub(r"\b[\w-]+(?:\.[\w-]+){2,}\b", "<host>", text)
    text = re.sub(r"\bport \d+\b", "port <port>", text, flags=re.I)
    text = re.sub(r"""(["'])(?:(?!\1).)*\1""", "<name>", text)
    text = " ".join(text.split())
    return text[:200]


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

    The Neon control-plane causes below were added after the serving database
    went unreachable for a day and this function's answer was "the cause was
    not recognised" — the one failure it exists to prevent. A managed Postgres
    can refuse a connection for reasons that are not the connection's fault:
    the compute is disabled, or the free-tier quota is spent. Neither says
    "authentication" or "timeout", so every branch here missed, and the page
    reported an unknown fault while the driver knew exactly what it was.
    """
    text = str(exc).lower()

    # Control-plane refusals first. These arrive as ordinary OperationalErrors
    # and read like connection faults, but no change to the URL will fix them —
    # they are settled in the Neon console, not in the environment.
    if "endpoint is disabled" in text or "endpoint has been disabled" in text:
        return (
            "The Neon compute endpoint is disabled, so the credential is never "
            "reached. Re-enable the endpoint in the Neon console; nothing in "
            "GRIDCAST_READONLY_DATABASE_URL needs to change."
        )
    # Before the plan-limit branch: "connection limit exceeded" is a full
    # server, not a spent plan, and the two need opposite responses. A bare
    # "exceeded" match would send an operator to the billing page over a
    # transient pool exhaustion.
    if (
        "too many connections" in text
        or "remaining connection slots" in text
        or "connection limit" in text
    ):
        return (
            "The database is refusing new connections because its limit is full. "
            "The serving URL should be the -pooler endpoint on Neon."
        )
    if (
        "quota" in text
        or "compute time" in text
        or "plan limit" in text
        or "usage limit" in text
        or "exceeded the limit" in text
        or "storage limit" in text
    ):
        return (
            "Neon refused the connection on a plan limit — the free tier's "
            "compute hours or storage are spent. Check usage in the Neon "
            "console. Retrying will not clear it and the URL is not at fault."
        )
    if "console request failed" in text or "control plane" in text:
        return (
            "Neon accepted the connection but could not start the compute. This "
            "is a control-plane failure, not a credential one — check the Neon "
            "console for the project's state before touching the URL."
        )
    if "is not permitted to log in" in text or "nologin" in text:
        return (
            "The serving role exists but has had LOGIN revoked. Restore it with "
            "ALTER ROLE gridcast_readonly LOGIN."
        )
    if "network is unreachable" in text or "no route to host" in text:
        return (
            "The serving container could not route to the database host, so no "
            "credential was ever sent. This is egress from the API, not the "
            "database. gridcast.db tries every IPv4 address before any IPv6 "
            "one and reports the first failure, so this is the IPv4 attempt "
            "talking; the serving logs name every address that was tried."
        )
    if "connection refused" in text:
        return (
            "The host answered and refused the port. The database is not "
            "listening where the URL says it is; check the host and port."
        )
    if "closed the connection unexpectedly" in text or "terminating connection" in text:
        return (
            "The database accepted the connection and then dropped it, which "
            "usually means the compute restarted mid-handshake. Reload once "
            "before investigating."
        )

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
    return (
        "The database could not be reached, and the cause was not recognised. "
        f"The driver said, with hosts and identifiers removed: {_redact(exc)}"
    )


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
        # The diagnosis is a keyword match, and a keyword match can be wrong.
        # It read one outage as a spent plan on the strength of the word
        # "exceeded", which is also how a server says its connection pool is
        # full. Publishing the redacted message alongside the interpretation
        # means the reader can check the second against the first instead of
        # taking it on trust — and it costs one more deploy cycle to find out
        # nothing when the classifier is right.
        payload["driver_message"] = _redact(exc)
        payload["warnings"].append(payload["diagnosis"])
        return payload

    payload["database"] = "ok"
    payload["spine"] = spine
    payload["recent_runs"] = runs

    if not runs:
        payload["warnings"].append("No pipeline runs recorded yet — expected before M1.")

    return payload
