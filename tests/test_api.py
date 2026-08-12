from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

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


def test_root_lists_entry_points() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "gridcast"
