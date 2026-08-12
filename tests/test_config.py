"""Settings behaviour.

Every Settings() here passes _env_file=None. Without it these tests read the
developer's local .env and assert against whatever happens to be in it — which
means they pass in CI, where no .env exists, and fail on the machine that wrote
them. A test whose result depends on ambient environment is testing the
environment, not the code.
"""

from __future__ import annotations

import pytest

from gridcast.config import Settings


def test_identical_roles_are_rejected() -> None:
    """If both URLs are the same role, the API can write to the register.

    That would void the append-only guarantee silently, which is the worst way
    for it to fail — the seals would still pass, because the writer and the
    auditor would be the same process.
    """
    settings = Settings(
        _env_file=None,
        database_url="postgresql://u:p@host/db",
        readonly_database_url="postgresql://u:p@host/db",
    )
    with pytest.raises(ValueError, match="identical"):
        settings.assert_roles_distinct()


def test_distinct_roles_are_accepted() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://app:p@host/db",
        readonly_database_url="postgresql://readonly:p@host/db",
    )
    settings.assert_roles_distinct()
    assert settings.readonly_role_in_use is True


def test_build_id_uses_the_platform_commit_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployed instance must not report itself as 'local'.

    Render injects RENDER_GIT_COMMIT; render.yaml cannot pass it through
    declaratively, because fromService only resolves host/port/connectionString.
    """
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def456")
    settings = Settings(_env_file=None)
    assert settings.build_id == "abc123def456"


def test_build_id_prefers_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def456")
    settings = Settings(_env_file=None, commit="explicit-value")
    assert settings.build_id == "explicit-value"


def test_build_id_falls_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RENDER_GIT_COMMIT", "VERCEL_GIT_COMMIT_SHA", "GITHUB_SHA"):
        monkeypatch.delenv(var, raising=False)
    assert Settings(_env_file=None).build_id == "local"


def test_missing_readonly_url_falls_back_but_is_reported() -> None:
    """Local convenience, but /v1/status must confess it."""
    settings = Settings(_env_file=None, database_url="postgresql://app:p@host/db")
    settings.assert_roles_distinct()  # no error: absence is not misconfiguration
    assert settings.serving_url == settings.database_url
    assert settings.readonly_role_in_use is False
