"""Shared HTTP plumbing for external providers.

Every request has a timeout and a bounded number of retries. Logs contain
only the provider name, HTTP status and exception class: never URLs (they
carry the user's text as a query parameter) and never request bodies.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "ukrainian-helper-bot/0.2 (+https://github.com/nktvlchk/ukrainian-bot)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ProviderError(Exception):
    """A provider failed. ``str(exc)`` is safe to log (no user text)."""

    def __init__(self, provider: str, reason: str):
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason


def make_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
    )


async def get_json(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    params: dict[str, Any],
    retries: int = 2,
    backoff: float = 0.5,
) -> Any:
    last_reason = "unknown"
    for attempt in range(retries + 1):
        try:
            response = await client.get(url, params=params)
        except httpx.TimeoutException:
            last_reason = "timeout"
        except httpx.HTTPError as exc:
            last_reason = type(exc).__name__
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    raise ProviderError(provider, "invalid JSON") from None
            last_reason = f"HTTP {response.status_code}"
            if response.status_code not in RETRY_STATUSES:
                break
        if attempt < retries:
            logger.info("%s attempt %d failed: %s", provider, attempt + 1, last_reason)
            await asyncio.sleep(backoff * (2**attempt))
    logger.warning("%s failed: %s", provider, last_reason)
    raise ProviderError(provider, last_reason)
