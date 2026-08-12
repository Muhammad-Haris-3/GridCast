from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers.status import _diagnose

client = TestClient(app)


def test_health_does_not_require_a_database() -> None:
    """A liveness probe that depends on Postgres reports the database's health.

    On a free tier that turns a sleeping database into an apparently dead
    service, and the platform restarts a container that was working fine.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_degrades_instead_of_failing() -> None:
    """With no database configured, /v1/status still answers."""
    response = client.get("/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] in {"ok", "unreachable"}
    assert "warnings" in body


@pytest.mark.parametrize(
    ("message", "expected_phrase"),
    [
        ('connection failed: ERROR: password authentication failed for role "x"', "rotated"),
        ('FATAL: role "gridcast_readonly" does not exist', "does not exist"),
        ("connection timeout expired", "waking"),
        ("could not translate host name to address", "hostname"),
        ("SSL connection has been closed unexpectedly", "sslmode=require"),
        ("something nobody has seen before", "not recognised"),
    ],
)
def test_connection_failures_are_diagnosed_not_just_named(
    message: str, expected_phrase: str
) -> None:
    """A status page that says only "OperationalError" helps nobody.

    A rejected password, an unreachable host and a missing role need three
    different fixes, and the person reading this is usually the one who can
    apply it in a minute — if told which it is.
    """
    assert expected_phrase in _diagnose(RuntimeError(message))


def test_diagnosis_never_echoes_the_driver_message() -> None:
    """This endpoint is public. Classify causes, never pass text through."""
    leaky = "connection to server at 1.2.3.4 failed: password authentication failed"
    diagnosis = _diagnose(RuntimeError(leaky))
    assert "1.2.3.4" not in diagnosis


def test_root_lists_entry_points() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "gridcast"
