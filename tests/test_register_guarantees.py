"""The append-only guarantee, tested against a real database.

Everything else in this project rests on one claim: no forecast was edited
after its outcome became known. These tests are the evidence that the claim is
enforced by the database rather than by the author's good intentions.

They connect as a member of gridcast_app — NOT as the owner or a superuser,
both of which bypass grants and would make the test pass while proving nothing.

Marked `db`. Run with:  pytest -m db   (requires GRIDCAST_DATABASE_URL)
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

pytestmark = pytest.mark.db

ADMIN_URL = os.environ.get("GRIDCAST_DATABASE_URL", "")
TEST_ROLE = "gridcast_app_probe"
TEST_PASSWORD = "probe_only_not_a_secret"  # noqa: S105 — ephemeral CI role


def _as_role(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(
        (parts.scheme, f"{user}:{password}@{host}{port}", parts.path, parts.query, "")
    )


@pytest.fixture(scope="module")
def app_role_url() -> str:
    if not ADMIN_URL:
        pytest.skip("GRIDCAST_DATABASE_URL not set")

    with psycopg.connect(ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (TEST_ROLE,))
        if cur.fetchone() is None:
            # CREATE ROLE takes no parameters, so the statement is composed with
            # psycopg.sql rather than interpolated. The values are module
            # constants, but composing correctly here keeps the habit intact for
            # the places where the values will not be constants.
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE gridcast_app").format(
                    sql.Identifier(TEST_ROLE), sql.Literal(TEST_PASSWORD)
                )
            )
        # CONNECT is granted to PUBLIC by default, so no explicit grant is needed.

    return _as_role(ADMIN_URL, TEST_ROLE, TEST_PASSWORD)


def _sample_forecast() -> dict[str, object]:
    run_at = datetime.now(UTC)
    return {
        "forecast_id": uuid.uuid4(),
        "model_version": "test-probe",
        "run_id": uuid.uuid4(),
        "run_at_utc": run_at,
        "target_sp_start_utc": run_at + timedelta(hours=3),
        "horizon_periods": 6,
        "point_gco2_kwh": 180.0,
        "code_commit": "test",
        "feature_snapshot_hash": b"\x00" * 32,
        "row_hash": b"\x01" * 32,
    }


def _insert(cur: psycopg.Cursor, row: dict[str, object]) -> None:
    cur.execute(
        """
        INSERT INTO register.reg_forecast_point
            (forecast_id, model_version, run_id, run_at_utc, target_sp_start_utc,
             horizon_periods, point_gco2_kwh, code_commit,
             feature_snapshot_hash, row_hash)
        VALUES
            (%(forecast_id)s, %(model_version)s, %(run_id)s, %(run_at_utc)s,
             %(target_sp_start_utc)s, %(horizon_periods)s, %(point_gco2_kwh)s,
             %(code_commit)s, %(feature_snapshot_hash)s, %(row_hash)s)
        """,
        row,
    )


def test_app_role_can_insert_a_forecast(app_role_url: str) -> None:
    row = _sample_forecast()
    with psycopg.connect(app_role_url, autocommit=True) as conn, conn.cursor() as cur:
        _insert(cur, row)
        cur.execute(
            "SELECT count(*) FROM register.reg_forecast_point WHERE forecast_id = %s",
            (row["forecast_id"],),
        )
        assert cur.fetchone()[0] == 1


def test_app_role_cannot_update_a_forecast(app_role_url: str) -> None:
    """The whole project in one assertion."""
    row = _sample_forecast()
    with psycopg.connect(app_role_url, autocommit=True) as conn, conn.cursor() as cur:
        _insert(cur, row)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "UPDATE register.reg_forecast_point SET point_gco2_kwh = 1 "
                "WHERE forecast_id = %s",
                (row["forecast_id"],),
            )


def test_app_role_cannot_delete_a_forecast(app_role_url: str) -> None:
    row = _sample_forecast()
    with psycopg.connect(app_role_url, autocommit=True) as conn, conn.cursor() as cur:
        _insert(cur, row)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "DELETE FROM register.reg_forecast_point WHERE forecast_id = %s",
                (row["forecast_id"],),
            )


def test_a_forecast_must_be_about_the_future(app_role_url: str) -> None:
    """A backdated forecast is the simplest possible way to fake accuracy."""
    row = _sample_forecast()
    row["target_sp_start_utc"] = row["run_at_utc"] - timedelta(hours=1)
    with (
        psycopg.connect(app_role_url, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        _insert(cur, row)


def test_quantiles_may_not_cross(app_role_url: str) -> None:
    row = _sample_forecast()
    row |= {
        "q025_gco2_kwh": 200.0,
        "q10_gco2_kwh": 190.0,
        "q90_gco2_kwh": 150.0,  # below q10 — inside-out interval
        "q975_gco2_kwh": 210.0,
    }
    with (
        psycopg.connect(app_role_url, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        cur.execute(
            """
                INSERT INTO register.reg_forecast_point
                    (forecast_id, model_version, run_id, run_at_utc,
                     target_sp_start_utc, horizon_periods, point_gco2_kwh,
                     q025_gco2_kwh, q10_gco2_kwh, q90_gco2_kwh, q975_gco2_kwh,
                     code_commit, feature_snapshot_hash, row_hash)
                VALUES
                    (%(forecast_id)s, %(model_version)s, %(run_id)s, %(run_at_utc)s,
                     %(target_sp_start_utc)s, %(horizon_periods)s, %(point_gco2_kwh)s,
                     %(q025_gco2_kwh)s, %(q10_gco2_kwh)s, %(q90_gco2_kwh)s,
                     %(q975_gco2_kwh)s, %(code_commit)s, %(feature_snapshot_hash)s,
                     %(row_hash)s)
            """,
            row,
        )


def test_the_same_forecast_cannot_be_issued_twice(app_role_url: str) -> None:
    """Grain uniqueness: one model, one issue time, one target, one forecast."""
    row = _sample_forecast()
    duplicate = dict(row)
    duplicate["forecast_id"] = uuid.uuid4()
    duplicate["point_gco2_kwh"] = 999.0

    with psycopg.connect(app_role_url, autocommit=True) as conn, conn.cursor() as cur:
        _insert(cur, row)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(cur, duplicate)
