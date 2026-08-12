"""Configuration, read from the environment.

Two database URLs, deliberately. The pipeline writes; the API reads. If they
resolve to the same role, the API can write to the forecast register and the
append-only guarantee is void — so :func:`Settings.assert_roles_distinct`
exists to say so out loud rather than let it pass silently.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GRIDCAST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = ""
    readonly_database_url: str = ""
    env: str = "local"
    commit: str = "local"

    # Design doc 6.2: the incremental lookback is a variable, not a literal,
    # so M2's measurement of the revision tail can change it without a code edit.
    lookback_days: int = 14

    # Design doc 6.2.1: maturity thresholds are set by M2 measurement (D-1).
    # These defaults are placeholders and are marked as such wherever surfaced.
    maturity_hours: int = 24
    stability_hours: int = 6

    @property
    def serving_url(self) -> str:
        """The URL the API should use. Falls back to the pipeline URL locally.

        The fallback is convenient for local development and dangerous in
        production, which is why /v1/status reports whether it is in effect.
        """
        return self.readonly_database_url or self.database_url

    @property
    def readonly_role_in_use(self) -> bool:
        return bool(self.readonly_database_url) and (
            self.readonly_database_url != self.database_url
        )

    def assert_roles_distinct(self) -> None:
        if self.readonly_database_url and self.readonly_database_url == self.database_url:
            raise ValueError(
                "GRIDCAST_READONLY_DATABASE_URL is identical to "
                "GRIDCAST_DATABASE_URL. The serving role must be a distinct "
                "read-only role, or the API can write to the forecast register."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
