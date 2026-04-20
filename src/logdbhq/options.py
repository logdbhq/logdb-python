"""Client + reader option types.

Splitting options from the client keeps the API discoverable — users can
introspect every tuning knob and its default without reading the client
source. Names mirror the other SDKs' option surfaces (
`@logdbhq/node`, `@logdbhq/web`, `logdbhq/logdb-php`) so porting apps
across runtimes is mostly a mechanical rename from camelCase to
snake_case.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


OnErrorCallback = Callable[[Exception, Optional[list]], None]
"""Signature for ``on_error`` hooks. Receives the exception and, for
batch failures, the batch of items that couldn't be delivered."""


def _env_default(name: str, fallback: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v else fallback


@dataclass
class LogDBClientOptions:
    """Configuration for :class:`~logdbhq.LogDBClient` /
    :class:`~logdbhq.AsyncLogDBClient`.

    Every field has a reasonable default so most callers only supply
    ``api_key`` (and usually ``default_application``). Ports from the
    Node SDK will find the names one-to-one with a snake_case rename.
    """

    api_key: Optional[str] = None
    """Server API key. Either supply here or via :envvar:`LOGDB_API_KEY`.
    Omitting both raises :class:`~logdbhq.LogDBConfigError` when the
    client tries to send."""

    endpoint: Optional[str] = None
    """Full REST API base URL (e.g. ``https://rest-api.logdb.site/rest-api``).
    When ``None``, the SDK discovers it at construction time from
    :attr:`discovery_url`. Set :envvar:`LOGDB_REST_URL` for a deploy-time
    override without editing code."""

    discovery_url: str = "https://discovery.logdb.site/get/rest-api"
    """Service-discovery URL. Called once per client; response is cached
    for the client's lifetime. Ignored when :attr:`endpoint` is set."""

    default_application: Optional[str] = None
    """Stamped as ``application`` on every log unless the call overrides."""

    default_environment: str = "production"
    """Stamped as ``environment``. Override per-log via the ``environment`` field."""

    default_collection: str = "logs"
    """Stamped as ``collection``. Override per-log via the ``collection`` field."""

    # ── Batching ───────────────────────────────────────────────────

    enable_batching: bool = True
    """When ``True``, ``log()`` returns immediately and the send happens
    on a timer / size threshold. Disable for must-not-lose events in
    serverless / single-shot contexts."""

    batch_size: int = 100
    """Max entries per type before flushing."""

    flush_interval: float = 5.0
    """Seconds an entry waits before being forced out (upper bound)."""

    max_batch_retries: int = 2
    """Retries per batch before falling back to per-item retry."""

    # ── Retry ──────────────────────────────────────────────────────

    max_retries: int = 3
    """Per-call retry attempts (after the first). Only transient errors
    (network, 5xx, timeouts) are retried — :class:`~logdbhq.LogDBAuthError`
    and :class:`~logdbhq.LogDBConfigError` fail fast."""

    retry_delay: float = 1.0
    """Initial backoff in seconds before the first retry."""

    retry_backoff_multiplier: float = 2.0
    """Exponential multiplier between retries. ±20% jitter is applied
    automatically."""

    # ── Circuit breaker ────────────────────────────────────────────

    enable_circuit_breaker: bool = True
    """Sliding-window failure-rate breaker protects upstream when it
    misbehaves. Disable for single-shot scripts."""

    circuit_breaker_failure_threshold: float = 0.5
    """Fraction of failures (0..1) in the sampling window that trips
    the breaker."""

    circuit_breaker_sampling_duration: float = 10.0
    """Rolling window in seconds the breaker evaluates."""

    circuit_breaker_duration_of_break: float = 30.0
    """How long the breaker stays open before transitioning to
    half-open (probe state)."""

    # ── Transport ──────────────────────────────────────────────────

    request_timeout: float = 30.0
    """Per-request deadline in seconds."""

    max_degree_of_parallelism: int = 4
    """Max concurrent sends during a batch flush. Sync client uses a
    :class:`concurrent.futures.ThreadPoolExecutor`; async client uses
    :class:`asyncio.Semaphore`."""

    headers: Dict[str, str] = field(default_factory=dict)
    """Extra HTTP headers on every request. Useful for tracing IDs or a
    proxy's auth token."""

    # ── Diagnostics ────────────────────────────────────────────────

    enable_debug_logging: bool = False
    """Log SDK lifecycle + request summaries via stdlib ``logging``."""

    on_error: Optional[OnErrorCallback] = None
    """Called for any delivery failure after retries are exhausted.
    Signature: ``(err: Exception, batch: list | None) -> None``."""

    # ── Convenience ────────────────────────────────────────────────

    @classmethod
    def from_env(cls, **overrides: object) -> "LogDBClientOptions":
        """Build options using environment variables as defaults.

        Recognized vars (overridden by any explicit kwarg):

        * :envvar:`LOGDB_API_KEY`
        * :envvar:`LOGDB_REST_URL` → ``endpoint``
        * :envvar:`LOGDB_DISCOVERY_URL` → ``discovery_url``
        * :envvar:`LOGDB_DEFAULT_APPLICATION`
        * :envvar:`LOGDB_DEFAULT_ENVIRONMENT`
        """
        env_map: Dict[str, object] = {}
        if (v := _env_default("LOGDB_API_KEY")) is not None:
            env_map["api_key"] = v
        if (v := _env_default("LOGDB_REST_URL")) is not None:
            env_map["endpoint"] = v
        if (v := _env_default("LOGDB_DISCOVERY_URL")) is not None:
            env_map["discovery_url"] = v
        if (v := _env_default("LOGDB_DEFAULT_APPLICATION")) is not None:
            env_map["default_application"] = v
        if (v := _env_default("LOGDB_DEFAULT_ENVIRONMENT")) is not None:
            env_map["default_environment"] = v
        env_map.update(overrides)
        return cls(**env_map)  # type: ignore[arg-type]


@dataclass
class LogDBReaderOptions:
    """Configuration for :class:`~logdbhq.LogDBReader` /
    :class:`~logdbhq.AsyncLogDBReader`."""

    api_key: Optional[str] = None
    """Server API key. :envvar:`LOGDB_API_KEY` by default."""

    endpoint: Optional[str] = None
    """Full REST API base URL. Discovered if ``None``. :envvar:`LOGDB_REST_URL`
    overrides."""

    discovery_url: str = "https://discovery.logdb.site/get/rest-api"
    """Service-discovery URL when :attr:`endpoint` is not explicit."""

    max_retries: int = 3
    retry_delay: float = 0.5
    retry_backoff_multiplier: float = 2.0
    request_timeout: float = 30.0
    headers: Dict[str, str] = field(default_factory=dict)
    enable_debug_logging: bool = False

    on_error: Optional[Callable[[Exception, str], None]] = None
    """Called after retries are exhausted.
    Signature: ``(err: Exception, endpoint_path: str) -> None``."""

    @classmethod
    def from_env(cls, **overrides: object) -> "LogDBReaderOptions":
        """Same env-var conventions as :meth:`LogDBClientOptions.from_env`."""
        env_map: Dict[str, object] = {}
        if (v := _env_default("LOGDB_API_KEY")) is not None:
            env_map["api_key"] = v
        if (v := _env_default("LOGDB_REST_URL")) is not None:
            env_map["endpoint"] = v
        if (v := _env_default("LOGDB_DISCOVERY_URL")) is not None:
            env_map["discovery_url"] = v
        env_map.update(overrides)
        return cls(**env_map)  # type: ignore[arg-type]
