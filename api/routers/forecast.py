"""Forecast and accuracy endpoints. Read-only, precomputed (SRS FR-25 to FR-29).

Every route reads rows that already exist. Nothing here computes a forecast,
trains anything, or scans history — the API imports neither scikit-learn nor
statsmodels, and a CI check enforces that.

The accuracy route carries a rule the rest of the project depends on: it refuses
to publish figures it cannot support. Below a minimum sample it returns the
counts and an explicit `publishable: false` rather than a number. A scoreboard
whose whole claim is honesty cannot open with three data points.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from gridcast.config import get_settings
from gridcast.db import fetch_all, fetch_one

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["forecast"])

# Below this many scored points in a horizon group, the figure is reported as
# not yet publishable. 200 is roughly two days of live operation at 96 horizons
# — enough that a single unusual day cannot dominate, and far below the 1,440
# the pre-registered promotion rule requires before any model comparison is
# decided (PREREGISTRATION 5).
MIN_PUBLISHABLE_N = 200


@router.get("/forecast/current")
def current_forecast() -> dict[str, Any]:
    """The champion's most recent forecast, with intervals."""
    latest = fetch_one(
        """
        SELECT m.model_version, max(f.run_at_utc) AS run_at_utc
          FROM register.reg_forecast_point f
          JOIN register.reg_model_version m ON m.model_version = f.model_version
         WHERE m.role = 'champion'
         GROUP BY m.model_version
        """,
        readonly=True,
    )
    if not latest or latest["run_at_utc"] is None:
        return {
            "model_version": None,
            "run_at_utc": None,
            "horizons": [],
            "detail": "no forecasts issued yet",
        }

    horizons = fetch_all(
        """
        SELECT horizon_periods, target_sp_start_utc,
               point_gco2_kwh, q10_gco2_kwh, q90_gco2_kwh,
               q025_gco2_kwh, q975_gco2_kwh
          FROM register.reg_forecast_point
         WHERE model_version = %s AND run_at_utc = %s
         ORDER BY horizon_periods
        """,
        (latest["model_version"], latest["run_at_utc"]),
        readonly=True,
    )
    return {
        "model_version": latest["model_version"],
        "run_at_utc": latest["run_at_utc"],
        "horizons": horizons,
    }


@router.get("/accuracy")
def accuracy(
    model: str | None = Query(None, description="Filter to one model version"),
) -> dict[str, Any]:
    """Live out-of-sample accuracy by model and horizon group.

    Every row carries `n`. Rows below MIN_PUBLISHABLE_N are returned with
    `publishable: false` and their metrics intact — suppressing the numbers
    entirely would hide how far off publication is, which is itself the most
    useful thing to know while a scoreboard is filling up.
    """
    rows = fetch_all(
        """
        SELECT model_version, horizon_group, n, mae, rmse, mase,
               interval_width_80, coverage_80, coverage_95,
               first_target, last_target
          FROM marts.mart_live_accuracy
         -- The ::text casts are required, not cosmetic. An untyped placeholder
         -- compared to NULL gives Postgres nothing to infer from, and the query
         -- fails with "could not determine data type of parameter $1".
         --
         -- Note also that a placeholder written inside a SQL comment still
         -- counts as one to psycopg, which is why this note does not contain
         -- the literal token.
         WHERE (%s::text IS NULL OR model_version = %s::text)
         ORDER BY horizon_group, mae
        """,
        (model, model),
        readonly=True,
    )
    for row in rows:
        row["publishable"] = row["n"] >= MIN_PUBLISHABLE_N

    # What the figures above leave out, published beside them.
    #
    # mart_live_accuracy drops scores a model issued in a configuration it was
    # not built for, because pooling those with valid ones produces a number
    # that describes neither. An exclusion nobody can see is an edit, so the
    # count and the reason travel with the payload rather than living in a
    # comment in the warehouse.
    #
    # None and [] mean different things and the page renders them differently:
    # [] is "nothing was excluded", None is "this build could not say". The
    # second happens between an API deploy and the next warehouse build, when
    # the relation does not exist yet — and it must degrade, because a status
    # surface that 500s during a rollout is the failure this project already
    # had once.
    try:
        excluded: list[dict[str, Any]] | None = fetch_all(
            """
            SELECT model_version, reason, n_excluded,
                   first_issued, last_issued, first_target, last_target
              FROM marts.mart_excluded_scores
             ORDER BY model_version
            """,
            readonly=True,
        )
    except Exception as exc:  # noqa: BLE001 — a missing relation is not an outage
        logger.warning("excluded-score accounting unavailable: %s", type(exc).__name__)
        excluded = None

    total = sum(row["n"] for row in rows)
    return {
        "min_publishable_n": MIN_PUBLISHABLE_N,
        "total_scored_points": total,
        "any_publishable": any(row["publishable"] for row in rows),
        "rows": rows,
        "excluded": excluded,
        "note": (
            "Live, out-of-sample, scored after the fact. Every model is scored on "
            "identical periods: a target is included only when every model issuing "
            "at that time forecast it."
        ),
    }


@router.get("/leaderboard")
def leaderboard() -> dict[str, Any]:
    """Models ranked within each horizon group, on identical periods."""
    rows = fetch_all(
        """
        SELECT horizon_group, model_version, n, mae, mase,
               rank() OVER (PARTITION BY horizon_group ORDER BY mae) AS position
          FROM marts.mart_live_accuracy
         ORDER BY horizon_group, position
        """,
        readonly=True,
    )
    return {
        "rows": rows,
        "note": (
            "ESO_published is National Grid ESO's own forecast, recorded at the "
            "horizon we received it. Unlike a backtest, this comparison is "
            "horizon-matched — which is the only reason it is a fair one."
        ),
    }


@router.get("/models")
def models() -> dict[str, Any]:
    """The model registry, including promotion history."""
    registry = fetch_all(
        """
        SELECT model_version, model_family, role, role_since_utc,
               uses_eso_forecast, created_at_utc, notes
          FROM register.reg_model_version
         ORDER BY role, model_version
        """,
        readonly=True,
    )
    promotions = fetch_all(
        """
        SELECT decided_at_utc, champion_version, challenger_version,
               outcome, preregistration_commit
          FROM register.reg_promotion_event
         ORDER BY decided_at_utc DESC
         LIMIT 20
        """,
        readonly=True,
    )
    return {
        "models": registry,
        "promotions": promotions,
        "note": (
            "Every promotion evaluation is recorded, including those that did not "
            "promote. A registry holding only successful promotions is evidence "
            "that failures went unrecorded, not that none occurred."
        ),
    }


@router.get("/integrity")
def integrity() -> dict[str, Any]:
    """Register size and the result of the most recent seal audit.

    Public because the guarantee is meaningless if only the operator can check
    it. The seal hashes are also committed to git, so this can be verified
    against a public history by someone with no access to the database.
    """
    register = fetch_one(
        """
        SELECT count(*) AS forecasts,
               count(DISTINCT model_version) AS models,
               min(run_at_utc) AS first_issued,
               max(run_at_utc) AS last_issued
          FROM register.reg_forecast_point
        """,
        readonly=True,
    )
    scored = fetch_one("SELECT count(*) AS scored FROM register.reg_forecast_score", readonly=True)
    seals = fetch_all(
        "SELECT period_month, row_count, sealed_at_utc FROM register.reg_forecast_seal "
        "ORDER BY period_month DESC LIMIT 12",
        readonly=True,
    )
    audits = fetch_all(
        "SELECT checked_at_utc, period_month, passed, expected_count, observed_count "
        "FROM register.reg_seal_audit ORDER BY checked_at_utc DESC LIMIT 5",
        readonly=True,
    )
    return {
        "serving_host": get_settings().serving_host,
        "register": register,
        "scored": scored["scored"] if scored else 0,
        "seals": seals,
        "recent_audits": audits,
        "guarantee": (
            "The application role holds INSERT on the forecast register and has no "
            "UPDATE or DELETE. Closed months are hashed and the hash is committed "
            "to git, so the live database can be checked against a public history."
        ),
    }
