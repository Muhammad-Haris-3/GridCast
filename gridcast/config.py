"""Configuration, read from the environment.

Two database URLs, deliberately. The pipeline writes; the API reads. If they
resolve to the same role, the API can write to the forecast register and the
append-only guarantee is void — so :func:`Settings.assert_roles_distinct`
exists to say so out loud rather than let it pass silently.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# An environment label is a short word. Anything else — most importantly a
# connection string pasted into the wrong variable — must never reach a response.
ENV_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")


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

    # Left empty so the platform's own build identifier can fill it in. Render
    # injects RENDER_GIT_COMMIT and Vercel injects VERCEL_GIT_COMMIT_SHA; see
    # build_id below. Defaulting this to "local" would mean a production
    # deployment quietly reporting itself as local.
    commit: str = ""

    # Design doc 6.2: the incremental lookback is a variable, not a literal,
    # so M2's measurement of the revision tail can change it without a code edit.
    lookback_days: int = 14

    # Design doc 6.2.1: maturity thresholds are set by M2 measurement (D-1).
    # These defaults are placeholders and are marked as such wherever surfaced.
    maturity_hours: int = 24
    stability_hours: int = 6

    @field_validator("env")
    @classmethod
    def _reject_anything_that_is_not_a_label(cls, value: str) -> str:
        """Never echo an unexpected value in the env field.

        /health and /v1/status are public and unauthenticated, and they report
        `env` verbatim. A connection string pasted into GRIDCAST_ENV by mistake
        would therefore be published to the internet, password included — which
        happened once during the first Render deployment and is the reason this
        validator exists.

        Anything that is not a short lowercase label becomes "misconfigured".
        Refusing to start would be worse: it would take the service down for a
        cosmetic field, and the operator would lose the status page that tells
        them what is wrong.
        """
        value = value.strip()
        return value if ENV_LABEL.match(value) else "misconfigured"

    @property
    def env_is_valid(self) -> bool:
        return self.env != "misconfigured"

    @property
    def build_id(self) -> str:
        """The commit this instance is running, whoever deployed it.

        Explicit configuration wins; otherwise the hosting platform's own
        injected variable is used. Reported by /health and /v1/status, so a
        deployed page can be traced back to the exact code behind it.
        """
        return (
            self.commit
            or os.environ.get("RENDER_GIT_COMMIT")
            or os.environ.get("VERCEL_GIT_COMMIT_SHA")
            or os.environ.get("GITHUB_SHA")
            or "local"
        )

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
