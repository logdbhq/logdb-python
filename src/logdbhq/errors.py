"""Error hierarchy for the LogDB Python SDK.

All SDK-originated exceptions descend from :class:`LogDBError` so callers
can catch a single type if they only care whether "something LogDB-related
broke". More-specific subclasses let callers differentiate retryable
transient failures (:class:`LogDBNetworkError`, :class:`LogDBTimeoutError`)
from permanent ones (:class:`LogDBAuthError`, :class:`LogDBConfigError`)
and the synchronous circuit-breaker rejection
(:class:`LogDBCircuitOpenError`).

Writer methods on :class:`~logdbhq.LogDBClient` return a
:class:`~logdbhq.LogResponseStatus` rather than raising these on transient
failure; exceptions surface via the ``on_error`` callback / event. Reader
methods on :class:`~logdbhq.LogDBReader` raise directly since reads can
not meaningfully continue without a response.
"""

from __future__ import annotations

from typing import Optional


class LogDBError(Exception):
    """Base class for every error this SDK raises."""

    def __init__(self, message: str, cause: Optional[BaseException] = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


class LogDBAuthError(LogDBError):
    """HTTP 401 or 403 — bad API key, wrong account, revoked token. Not retried."""


class LogDBConfigError(LogDBError):
    """HTTP 400 / 404 / 422 or construction-time validation failure. Not retried."""


class LogDBNetworkError(LogDBError):
    """Transport-level failure after retries are exhausted. 5xx, connection reset, DNS, etc."""


class LogDBTimeoutError(LogDBError):
    """Per-request deadline exceeded (:attr:`~logdbhq.LogDBClientOptions.request_timeout`)."""


class LogDBCircuitOpenError(LogDBError):
    """Raised synchronously when the circuit breaker is open — no network call was attempted."""

    def __init__(self, message: str = "LogDB circuit breaker is open") -> None:
        super().__init__(message)
