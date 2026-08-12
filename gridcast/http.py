"""HTTP client for the three upstream APIs.

Pipeline-only: the serving container never imports this module, and CI asserts
that httpx is absent from requirements-serve.txt.

These are free, public, unfunded services. Being a polite client is not
optional courtesy — an impolite one gets the whole project blocked.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

USER_AGENT = "GridCast/0.1 (portfolio project; https://github.com/Muhammad-Haris-3/GridCast)"

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class UpstreamError(RuntimeError):
    """A non-retryable upstream failure."""


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def get_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    """GET a URL and return parsed JSON, retrying transient failures.

    The return type is deliberately loose: Open-Meteo answers a
    multi-coordinate request with a list and a single-coordinate request with an
    object, and pretending otherwise would mean lying in the annotation.

    4xx other than 429 are not retried: a malformed request will stay malformed
    however many times it is sent, and retrying it just wastes someone else's
    bandwidth.
    """
    with httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(url, params=params)

        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()  # retryable

        if response.status_code >= 400:
            raise UpstreamError(f"{response.status_code} from {url}: {response.text[:300]}")

        return response.json()
