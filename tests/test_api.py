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
        # Managed-Postgres refusals. None of these are the connection's fault,
        # and none of them contain a word the original classifier looked for —
        # which is how the status page came to report an unknown cause while
        # the driver knew exactly what it was.
        ("ERROR: The endpoint has been disabled. Enable it in the console", "disabled"),
        ("ERROR: Your project has exceeded the compute time quota", "plan limit"),
        ("ERROR: Console request failed with status 500", "control-plane"),
        ('FATAL: role "gridcast_readonly" is not permitted to log in', "LOGIN"),
        ("FATAL: too many connections for role", "pooler"),
        ("connection failed: Network is unreachable", "egress"),
        ("connection to server failed: Connection refused", "refused the port"),
        ("server closed the connection unexpectedly", "restarted mid-handshake"),
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


def test_an_unrecognised_cause_still_says_something_usable() -> None:
    """ "Not recognised" on its own is the failure this page exists to prevent.

    An unknown cause is exactly when the operator most needs the driver's own
    words — but this endpoint is public, so they arrive with everything that
    locates the deployment taken out of them.
    """
    unknown = (
        'connection to server at "ep-sweet-unit-pooler.us-east-2.aws.neon.tech" '
        "(18.226.144.228), port 5432 failed: some brand new thing"
    )
    diagnosis = _diagnose(RuntimeError(unknown))

    assert "some brand new thing" in diagnosis
    assert "18.226.144.228" not in diagnosis
    assert "neon.tech" not in diagnosis
    assert "5432" not in diagnosis


def test_root_lists_entry_points() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "gridcast"
