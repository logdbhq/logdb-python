"""Service discovery for the LogDB REST API.

Matches the pattern used by ``@logdbhq/node`` and ``@logdbhq/web``: a small
HTTP ``GET`` against a well-known discovery URL returns the base URL of
the current rest-api deployment. Response body is either a bare URL or a
JSON-encoded string (``"https://…/rest-api"``), handled transparently.

This client caches the discovered URL for the lifetime of the enclosing
:class:`~logdbhq.LogDBClient` / :class:`~logdbhq.LogDBReader`. If
discovery fails, the error surfaces once on the first call and callers
are expected to retry (or set :attr:`~logdbhq.LogDBClientOptions.endpoint`
explicitly to bypass discovery entirely).
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from .errors import LogDBConfigError


_URL_LIKE = re.compile(r"^https?://", re.IGNORECASE)


def _normalize(raw: str) -> str:
    """Strip surrounding JSON quotes and trailing slashes. Validate shape."""
    text = raw.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    if not _URL_LIKE.match(text):
        raise LogDBConfigError(
            f"Discovery returned an unexpected body (not a URL): {text[:200]!r}"
        )
    return text.rstrip("/")


def discover_sync(discovery_url: str, *, timeout: float = 10.0) -> str:
    """Synchronously fetch the REST API base URL from *discovery_url*.

    Raises :class:`LogDBConfigError` on HTTP failures or malformed bodies.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(discovery_url)
    except httpx.HTTPError as exc:
        raise LogDBConfigError(
            f"Discovery request to {discovery_url} failed: {exc}"
        ) from exc

    if response.status_code != 200:
        raise LogDBConfigError(
            f"Discovery {discovery_url} returned HTTP {response.status_code}"
        )

    return _normalize(response.text)


async def discover_async(discovery_url: str, *, timeout: float = 10.0) -> str:
    """Asynchronously fetch the REST API base URL from *discovery_url*.

    Same semantics as :func:`discover_sync`."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(discovery_url)
    except httpx.HTTPError as exc:
        raise LogDBConfigError(
            f"Discovery request to {discovery_url} failed: {exc}"
        ) from exc

    if response.status_code != 200:
        raise LogDBConfigError(
            f"Discovery {discovery_url} returned HTTP {response.status_code}"
        )

    return _normalize(response.text)


class EndpointResolver:
    """One-shot, thread-safe cache over a discovered REST endpoint.

    Sync and async clients share this type. Each resolver instance
    performs at most one successful discovery; errors are not cached,
    so a transient outage doesn't lock the client into failure mode."""

    def __init__(
        self,
        *,
        explicit_endpoint: Optional[str],
        discovery_url: str,
        timeout: float = 10.0,
    ) -> None:
        self._explicit = explicit_endpoint.rstrip("/") if explicit_endpoint else None
        self._discovery_url = discovery_url
        self._timeout = timeout
        self._cached: Optional[str] = None

    def resolve_sync(self) -> str:
        if self._explicit is not None:
            return self._explicit
        if self._cached is not None:
            return self._cached
        self._cached = discover_sync(self._discovery_url, timeout=self._timeout)
        return self._cached

    async def resolve_async(self) -> str:
        if self._explicit is not None:
            return self._explicit
        if self._cached is not None:
            return self._cached
        self._cached = await discover_async(self._discovery_url, timeout=self._timeout)
        return self._cached
