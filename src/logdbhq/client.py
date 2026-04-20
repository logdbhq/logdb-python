"""Primary client classes.

:class:`LogDBClient` (blocking) is the default — safe inside web
frameworks, workers, notebooks, scripts. :class:`AsyncLogDBClient`
mirrors the same API for :mod:`asyncio` code (FastAPI, aiohttp, trio
via anyio, etc.). They intentionally share no state so a single process
can instantiate both.

Write methods return a :class:`~logdbhq.LogResponseStatus` rather than
raising on transient failure. The rationale: logging-path errors should
not break your app — they're observability signals, not primary
functionality. Catch the status if you want, or subscribe to
``on_error`` for batch-level failures.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterable, Iterator, List, Optional

import httpx

from ._transport import (
    build_headers,
    raise_for_status,
    serialize_body,
    translate_request_error,
)
from .batching import AsyncBatcher, SyncBatcher
from .discovery import EndpointResolver
from .errors import (
    LogDBAuthError,
    LogDBCircuitOpenError,
    LogDBConfigError,
    LogDBError,
    LogDBTimeoutError,
)
from .models import Log, LogBeat, LogCache, LogResponseStatus
from .options import LogDBClientOptions
from .resilience import (
    CircuitBreaker,
    call_with_retry_async,
    call_with_retry_sync,
)


_log = logging.getLogger("logdbhq.client")


# Wire-format path map. The server's routes pre-date this SDK; we just
# mirror them faithfully.
_WRITE_PATHS_SINGLE = {
    "log": "/log/event",
    "logBeat": "/log/beat",
    "logCache": "/log/cache",
}
_WRITE_PATHS_BATCH = {
    "log": "/log/event/batch",
    "logBeat": "/log/beat/batch",
    "logCache": "/log/cache/batch",
}


def _classify_status(exc: BaseException) -> LogResponseStatus:
    if isinstance(exc, LogDBAuthError):
        return LogResponseStatus.NotAuthorized
    if isinstance(exc, LogDBTimeoutError):
        return LogResponseStatus.Timeout
    if isinstance(exc, LogDBCircuitOpenError):
        return LogResponseStatus.CircuitOpen
    return LogResponseStatus.Failed


def _stamp_defaults_log(log: Log, opts: LogDBClientOptions) -> Log:
    # Stamp defaults non-destructively — per-call values always win.
    if log.application is None and opts.default_application is not None:
        log.application = opts.default_application
    if log.environment is None:
        log.environment = opts.default_environment
    if log.collection is None:
        log.collection = opts.default_collection
    if log.apiKey is None and opts.api_key is not None:
        log.apiKey = opts.api_key
    return log


def _stamp_defaults_beat(beat: LogBeat, opts: LogDBClientOptions) -> LogBeat:
    if beat.environment is None:
        beat.environment = opts.default_environment
    if beat.collection is None:
        beat.collection = opts.default_collection
    if beat.apiKey is None and opts.api_key is not None:
        beat.apiKey = opts.api_key
    return beat


def _stamp_defaults_cache(cache: LogCache, opts: LogDBClientOptions) -> LogCache:
    if cache.apiKey is None and opts.api_key is not None:
        cache.apiKey = opts.api_key
    return cache


# ──────────────────────────────────────────────────────────────────────
# Sync
# ──────────────────────────────────────────────────────────────────────


class LogDBClient:
    """Blocking LogDB writer.

    Example::

        from logdbhq import LogDBClient, Log, LogLevel

        client = LogDBClient(api_key="sk-...", default_application="my-app")
        client.log(Log(message="ready", level=LogLevel.Info))
        client.flush()
        client.close()
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        options: Optional[LogDBClientOptions] = None,
        **kwargs: object,
    ) -> None:
        if options is None:
            options = LogDBClientOptions(api_key=api_key, **kwargs)  # type: ignore[arg-type]
        elif api_key is not None:
            options.api_key = api_key

        if not options.api_key:
            raise LogDBConfigError(
                "api_key is required (pass it explicitly, via options, or LOGDB_API_KEY env var)"
            )

        self._opts = options
        self._resolver = EndpointResolver(
            explicit_endpoint=options.endpoint,
            discovery_url=options.discovery_url,
            timeout=min(options.request_timeout, 10.0),
        )
        self._http = httpx.Client(timeout=options.request_timeout)
        self._breaker = (
            CircuitBreaker(
                failure_threshold=options.circuit_breaker_failure_threshold,
                sampling_duration=options.circuit_breaker_sampling_duration,
                duration_of_break=options.circuit_breaker_duration_of_break,
            )
            if options.enable_circuit_breaker
            else None
        )

        # Batching is lazy — if the user never calls a write, we never
        # spin up a background thread.
        self._batcher: Optional[SyncBatcher] = None
        self._closed = False

    # ── Public API ─────────────────────────────────────────────────

    def log(self, log: Log) -> LogResponseStatus:
        return self._dispatch("log", _stamp_defaults_log(log, self._opts))

    def log_beat(self, beat: LogBeat) -> LogResponseStatus:
        return self._dispatch("logBeat", _stamp_defaults_beat(beat, self._opts))

    def log_cache(self, cache: LogCache) -> LogResponseStatus:
        return self._dispatch("logCache", _stamp_defaults_cache(cache, self._opts))

    def send_log_batch(self, logs: Iterable[Log]) -> LogResponseStatus:
        items = [_stamp_defaults_log(l, self._opts) for l in logs]
        return self._send_direct_batch("log", items)

    def send_log_beat_batch(self, beats: Iterable[LogBeat]) -> LogResponseStatus:
        items = [_stamp_defaults_beat(b, self._opts) for b in beats]
        return self._send_direct_batch("logBeat", items)

    def send_log_cache_batch(self, caches: Iterable[LogCache]) -> LogResponseStatus:
        items = [_stamp_defaults_cache(c, self._opts) for c in caches]
        return self._send_direct_batch("logCache", items)

    def flush(self) -> None:
        """Force any buffered entries out. Blocks until sends complete."""
        if self._batcher is not None:
            self._batcher.flush()

    def close(self) -> None:
        """Release the batcher, flush pending, close the HTTP client."""
        if self._closed:
            return
        self._closed = True
        if self._batcher is not None:
            self._batcher.close()
        self._http.close()

    # Context manager sugar: `with LogDBClient(...) as c: ...`.
    def __enter__(self) -> "LogDBClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ── Internals ──────────────────────────────────────────────────

    def _dispatch(self, kind: str, payload: object) -> LogResponseStatus:
        if self._closed:
            raise LogDBConfigError("LogDBClient is closed")

        if self._opts.enable_batching:
            self._ensure_batcher().enqueue(kind, payload)
            return LogResponseStatus.Success
        return self._send_direct_batch(kind, [payload])

    def _ensure_batcher(self) -> SyncBatcher:
        if self._batcher is None:
            self._batcher = SyncBatcher(
                send_batch=self._background_send,
                batch_size=self._opts.batch_size,
                flush_interval=self._opts.flush_interval,
                on_error=self._opts.on_error,
            )
        return self._batcher

    def _background_send(self, kind: str, items: List[object]) -> None:
        # Called from the batcher's thread. Uses the same resilience wrapping
        # as the direct path but preserves the fire-and-forget contract.
        self._send_with_resilience(kind, items, batch=True)

    def _send_direct_batch(self, kind: str, items: List[object]) -> LogResponseStatus:
        if not items:
            return LogResponseStatus.Success
        try:
            self._send_with_resilience(kind, items, batch=len(items) > 1)
            return LogResponseStatus.Success
        except LogDBError as exc:
            if self._opts.on_error:
                try:
                    self._opts.on_error(exc, items)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: on_error callback itself failed")
            return _classify_status(exc)

    def _send_with_resilience(
        self, kind: str, items: List[object], *, batch: bool
    ) -> None:
        if self._breaker and not self._breaker.should_allow():
            raise LogDBCircuitOpenError()

        def _do() -> None:
            self._raw_send(kind, items, batch=batch)

        try:
            call_with_retry_sync(
                _do,
                max_retries=self._opts.max_retries,
                retry_delay=self._opts.retry_delay,
                retry_backoff_multiplier=self._opts.retry_backoff_multiplier,
            )
            if self._breaker:
                self._breaker.record_success()
        except BaseException:
            if self._breaker:
                self._breaker.record_failure()
            raise

    def _raw_send(self, kind: str, items: List[object], *, batch: bool) -> None:
        endpoint = self._resolver.resolve_sync()
        path = (
            _WRITE_PATHS_BATCH[kind] if batch else _WRITE_PATHS_SINGLE[kind]
        )
        url = endpoint + path
        body_obj: object = items if batch else items[0]
        body = serialize_body(body_obj)
        headers = build_headers(api_key=self._opts.api_key, extra=self._opts.headers)

        try:
            response = self._http.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise translate_request_error(exc, url) from exc

        raise_for_status(response)


# ──────────────────────────────────────────────────────────────────────
# Async
# ──────────────────────────────────────────────────────────────────────


class AsyncLogDBClient:
    """:mod:`asyncio` equivalent of :class:`LogDBClient`.

    Same API, every write is ``await``-ed. Use as an async context
    manager so the background batcher task is cleanly shut down::

        async with AsyncLogDBClient(api_key="sk-...") as client:
            await client.log(Log(message="hi", level=LogLevel.Info))
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        options: Optional[LogDBClientOptions] = None,
        **kwargs: object,
    ) -> None:
        if options is None:
            options = LogDBClientOptions(api_key=api_key, **kwargs)  # type: ignore[arg-type]
        elif api_key is not None:
            options.api_key = api_key

        if not options.api_key:
            raise LogDBConfigError(
                "api_key is required (pass it explicitly, via options, or LOGDB_API_KEY env var)"
            )

        self._opts = options
        self._resolver = EndpointResolver(
            explicit_endpoint=options.endpoint,
            discovery_url=options.discovery_url,
            timeout=min(options.request_timeout, 10.0),
        )
        self._http = httpx.AsyncClient(timeout=options.request_timeout)
        self._breaker = (
            CircuitBreaker(
                failure_threshold=options.circuit_breaker_failure_threshold,
                sampling_duration=options.circuit_breaker_sampling_duration,
                duration_of_break=options.circuit_breaker_duration_of_break,
            )
            if options.enable_circuit_breaker
            else None
        )
        self._batcher: Optional[AsyncBatcher] = None
        self._closed = False

    # ── Public API ─────────────────────────────────────────────────

    async def log(self, log: Log) -> LogResponseStatus:
        return await self._dispatch("log", _stamp_defaults_log(log, self._opts))

    async def log_beat(self, beat: LogBeat) -> LogResponseStatus:
        return await self._dispatch("logBeat", _stamp_defaults_beat(beat, self._opts))

    async def log_cache(self, cache: LogCache) -> LogResponseStatus:
        return await self._dispatch("logCache", _stamp_defaults_cache(cache, self._opts))

    async def send_log_batch(self, logs: Iterable[Log]) -> LogResponseStatus:
        items = [_stamp_defaults_log(l, self._opts) for l in logs]
        return await self._send_direct_batch("log", items)

    async def send_log_beat_batch(self, beats: Iterable[LogBeat]) -> LogResponseStatus:
        items = [_stamp_defaults_beat(b, self._opts) for b in beats]
        return await self._send_direct_batch("logBeat", items)

    async def send_log_cache_batch(self, caches: Iterable[LogCache]) -> LogResponseStatus:
        items = [_stamp_defaults_cache(c, self._opts) for c in caches]
        return await self._send_direct_batch("logCache", items)

    async def flush(self) -> None:
        if self._batcher is not None:
            await self._batcher.flush()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._batcher is not None:
            await self._batcher.close()
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncLogDBClient":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    # ── Internals ──────────────────────────────────────────────────

    async def _dispatch(self, kind: str, payload: object) -> LogResponseStatus:
        if self._closed:
            raise LogDBConfigError("AsyncLogDBClient is closed")

        if self._opts.enable_batching:
            self._ensure_batcher().enqueue(kind, payload)
            return LogResponseStatus.Success
        return await self._send_direct_batch(kind, [payload])

    def _ensure_batcher(self) -> AsyncBatcher:
        if self._batcher is None:
            self._batcher = AsyncBatcher(
                send_batch=self._background_send,
                batch_size=self._opts.batch_size,
                flush_interval=self._opts.flush_interval,
                on_error=self._opts.on_error,
            )
        return self._batcher

    async def _background_send(self, kind: str, items: List[object]) -> None:
        await self._send_with_resilience(kind, items, batch=True)

    async def _send_direct_batch(self, kind: str, items: List[object]) -> LogResponseStatus:
        if not items:
            return LogResponseStatus.Success
        try:
            await self._send_with_resilience(kind, items, batch=len(items) > 1)
            return LogResponseStatus.Success
        except LogDBError as exc:
            if self._opts.on_error:
                try:
                    self._opts.on_error(exc, items)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: on_error callback itself failed")
            return _classify_status(exc)

    async def _send_with_resilience(
        self, kind: str, items: List[object], *, batch: bool
    ) -> None:
        if self._breaker and not self._breaker.should_allow():
            raise LogDBCircuitOpenError()

        async def _do() -> None:
            await self._raw_send(kind, items, batch=batch)

        try:
            await call_with_retry_async(
                _do,
                max_retries=self._opts.max_retries,
                retry_delay=self._opts.retry_delay,
                retry_backoff_multiplier=self._opts.retry_backoff_multiplier,
            )
            if self._breaker:
                self._breaker.record_success()
        except BaseException:
            if self._breaker:
                self._breaker.record_failure()
            raise

    async def _raw_send(self, kind: str, items: List[object], *, batch: bool) -> None:
        endpoint = await self._resolver.resolve_async()
        path = (
            _WRITE_PATHS_BATCH[kind] if batch else _WRITE_PATHS_SINGLE[kind]
        )
        url = endpoint + path
        body_obj: object = items if batch else items[0]
        body = serialize_body(body_obj)
        headers = build_headers(api_key=self._opts.api_key, extra=self._opts.headers)

        try:
            response = await self._http.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise translate_request_error(exc, url) from exc

        raise_for_status(response)


# ──────────────────────────────────────────────────────────────────────
# Convenience
# ──────────────────────────────────────────────────────────────────────


@contextmanager
def logdb_client(**kwargs: object) -> Iterator[LogDBClient]:
    """Shorthand context manager for one-off scripts::

        with logdb_client(api_key="...", default_application="cron") as c:
            c.log(Log(message="ran", level=LogLevel.Info))
    """
    client = LogDBClient(**kwargs)  # type: ignore[arg-type]
    try:
        yield client
    finally:
        client.close()
