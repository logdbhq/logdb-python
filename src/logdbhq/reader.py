"""Query-side client: :class:`LogDBReader` and :class:`AsyncLogDBReader`.

Unlike the writers, reader methods raise exceptions on failure — reads
can't meaningfully "best effort". Retry + circuit breaker wrap each
request via :mod:`resilience`.

Endpoints mirror the PHP SDK's ``/rest-api/log/sdk/*`` layout exactly so
a tenant configured for one SDK works with any of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ._transport import (
    build_headers,
    raise_for_status,
    serialize_body,
    translate_request_error,
)
from .discovery import EndpointResolver
from .errors import LogDBConfigError
from .models import (
    EventLogStatus,
    LogBeatEntry,
    LogCacheEntry,
    LogEntry,
    LogPage,
)
from .options import LogDBReaderOptions
from .resilience import call_with_retry_async, call_with_retry_sync


_log = logging.getLogger("logdbhq.reader")


# ──────────────────────────────────────────────────────────────────────
# Query params
# ──────────────────────────────────────────────────────────────────────


@dataclass
class BaseQueryParams:
    """Shared paging / sort fields. All reader param types embed these."""

    skip: int = 0
    take: int = 50
    sortField: str = "Timestamp"
    sortAscending: bool = False


@dataclass
class LogQueryParams(BaseQueryParams):
    """Filter shape for :meth:`LogDBReader.get_logs`. Every filter is
    optional; unset fields are simply omitted from the wire payload
    (via :func:`logdbhq._transport.serialize_body`)."""

    application: Optional[str] = None
    environment: Optional[str] = None
    level: Optional[str] = None
    collection: Optional[str] = None
    correlationId: Optional[str] = None
    source: Optional[str] = None
    userEmail: Optional[str] = None
    userId: Optional[int] = None
    httpMethod: Optional[str] = None
    requestPath: Optional[str] = None
    ipAddress: Optional[str] = None
    statusCode: Optional[int] = None
    searchString: Optional[str] = None
    isException: Optional[bool] = None
    fromDate: Optional[datetime] = None
    toDate: Optional[datetime] = None


@dataclass
class LogBeatQueryParams(BaseQueryParams):
    """Filter shape for :meth:`LogDBReader.get_log_beats`."""

    measurement: Optional[str] = None
    collection: Optional[str] = None
    tagFilters: Optional[Dict[str, str]] = None
    fromDate: Optional[datetime] = None
    toDate: Optional[datetime] = None


@dataclass
class LogCacheQueryParams(BaseQueryParams):
    """Filter shape for :meth:`LogDBReader.get_log_caches`."""

    keyPattern: Optional[str] = None
    collection: Optional[str] = None
    fromDate: Optional[datetime] = None
    toDate: Optional[datetime] = None

    sortField: str = "CreatedAt"
    """Cache rows are sorted by creation time by default, not timestamp."""


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

_LOG_QUERY_PATH = "/log/sdk/event/query"
_CACHE_QUERY_PATH = "/log/sdk/cache/query"
_BEAT_QUERY_PATH = "/log/sdk/beat/query"
_DISTINCT_COLLECTIONS_PATH = "/log/sdk/distinct-values/collection"
_EVENT_LOG_STATUS_PATH = "/log/sdk/event-log-status"


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _inflate_log_entry(raw: Dict[str, Any]) -> LogEntry:
    entry = LogEntry(raw=raw)
    for k, v in raw.items():
        if hasattr(entry, k) and k != "raw":
            # Parse timestamps on known timestamp-bearing fields.
            if k in ("timestamp",) and isinstance(v, str):
                setattr(entry, k, _parse_dt(v))
            elif k == "attributesD" and isinstance(v, dict):
                setattr(entry, k, {ak: _parse_dt(av) or av for ak, av in v.items()})
            else:
                setattr(entry, k, v)
    return entry


def _inflate_beat_entry(raw: Dict[str, Any]) -> LogBeatEntry:
    entry = LogBeatEntry(raw=raw)
    for k, v in raw.items():
        if hasattr(entry, k) and k != "raw":
            if k == "timestamp" and isinstance(v, str):
                setattr(entry, k, _parse_dt(v))
            else:
                setattr(entry, k, v)
    return entry


def _inflate_cache_entry(raw: Dict[str, Any]) -> LogCacheEntry:
    entry = LogCacheEntry(raw=raw)
    for k, v in raw.items():
        if hasattr(entry, k) and k != "raw":
            if k in ("createdAt", "updatedAt") and isinstance(v, str):
                setattr(entry, k, _parse_dt(v))
            else:
                setattr(entry, k, v)
    return entry


def _inflate_log_page(payload: Dict[str, Any], kind: str) -> LogPage:
    items_raw = payload.get("items") or []
    inflater = {
        "log": _inflate_log_entry,
        "beat": _inflate_beat_entry,
        "cache": _inflate_cache_entry,
    }[kind]
    return LogPage(
        items=[inflater(it) for it in items_raw],
        totalCount=int(payload.get("totalCount", 0)),
        page=int(payload.get("page", 0)),
        pageSize=int(payload.get("pageSize", 0)),
        hasMore=bool(payload.get("hasMore", False)),
    )


# ──────────────────────────────────────────────────────────────────────
# Sync reader
# ──────────────────────────────────────────────────────────────────────


class LogDBReader:
    """Blocking query client.

    Every method throws on failure — there's no best-effort fallback
    like on the writer. Catch :class:`~logdbhq.LogDBError` or one of its
    subclasses to handle failures uniformly.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        options: Optional[LogDBReaderOptions] = None,
        **kwargs: object,
    ) -> None:
        if options is None:
            options = LogDBReaderOptions(api_key=api_key, **kwargs)  # type: ignore[arg-type]
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

    def get_logs(self, params: Optional[LogQueryParams] = None) -> LogPage:
        """Paged log query. See :class:`LogQueryParams` for filters."""
        return _inflate_log_page(
            self._post(_LOG_QUERY_PATH, params or LogQueryParams()), "log"
        )

    def get_log_caches(
        self, params: Optional[LogCacheQueryParams] = None
    ) -> LogPage:
        return _inflate_log_page(
            self._post(_CACHE_QUERY_PATH, params or LogCacheQueryParams()), "cache"
        )

    def get_log_beats(
        self, params: Optional[LogBeatQueryParams] = None
    ) -> LogPage:
        return _inflate_log_page(
            self._post(_BEAT_QUERY_PATH, params or LogBeatQueryParams()), "beat"
        )

    def get_logs_count(self, params: Optional[LogQueryParams] = None) -> int:
        """Same filter shape as :meth:`get_logs` but only returns the count
        — avoids round-tripping large result sets when you just need a total."""
        page = self.get_logs(
            LogQueryParams(
                **{**(params.__dict__ if params else {}), "take": 0}
            ) if params else LogQueryParams(take=0)
        )
        return page.totalCount

    def get_collections(self) -> List[str]:
        body = self._get(_DISTINCT_COLLECTIONS_PATH)
        if isinstance(body, list):
            return [str(x) for x in body]
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            return [str(x) for x in body["items"]]
        return []

    def get_event_log_status(self) -> EventLogStatus:
        body = self._get(_EVENT_LOG_STATUS_PATH)
        if not isinstance(body, dict):
            return EventLogStatus()
        return EventLogStatus(
            hasWindowsEvents=bool(body.get("hasWindowsEvents", False)),
            hasIISEvents=bool(body.get("hasIISEvents", False)),
            hasWindowsMetrics=bool(body.get("hasWindowsMetrics", False)),
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LogDBReader":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ── Internals ──────────────────────────────────────────────────

    def _post(self, path: str, params: object) -> Dict[str, Any]:
        url = self._resolver.resolve_sync() + path
        body = serialize_body(params)
        headers = build_headers(api_key=self._opts.api_key, extra=self._opts.headers)

        def _do() -> httpx.Response:
            try:
                return self._http.post(url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                raise translate_request_error(exc, url) from exc

        try:
            response = call_with_retry_sync(
                _do,
                max_retries=self._opts.max_retries,
                retry_delay=self._opts.retry_delay,
                retry_backoff_multiplier=self._opts.retry_backoff_multiplier,
            )
        except Exception as exc:
            if self._opts.on_error:
                try:
                    self._opts.on_error(exc, path)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: reader on_error callback itself failed")
            raise

        raise_for_status(response)
        data = response.json()
        return data if isinstance(data, dict) else {"items": data}

    def _get(self, path: str) -> Any:
        url = self._resolver.resolve_sync() + path
        headers = build_headers(api_key=self._opts.api_key, extra=self._opts.headers)

        def _do() -> httpx.Response:
            try:
                return self._http.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise translate_request_error(exc, url) from exc

        try:
            response = call_with_retry_sync(
                _do,
                max_retries=self._opts.max_retries,
                retry_delay=self._opts.retry_delay,
                retry_backoff_multiplier=self._opts.retry_backoff_multiplier,
            )
        except Exception as exc:
            if self._opts.on_error:
                try:
                    self._opts.on_error(exc, path)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: reader on_error callback itself failed")
            raise
        raise_for_status(response)
        return response.json()


# ──────────────────────────────────────────────────────────────────────
# Async reader
# ──────────────────────────────────────────────────────────────────────


class AsyncLogDBReader:
    """:mod:`asyncio` equivalent of :class:`LogDBReader`."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        options: Optional[LogDBReaderOptions] = None,
        **kwargs: object,
    ) -> None:
        if options is None:
            options = LogDBReaderOptions(api_key=api_key, **kwargs)  # type: ignore[arg-type]
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

    async def get_logs(self, params: Optional[LogQueryParams] = None) -> LogPage:
        return _inflate_log_page(
            await self._post(_LOG_QUERY_PATH, params or LogQueryParams()), "log"
        )

    async def get_log_caches(
        self, params: Optional[LogCacheQueryParams] = None
    ) -> LogPage:
        return _inflate_log_page(
            await self._post(_CACHE_QUERY_PATH, params or LogCacheQueryParams()),
            "cache",
        )

    async def get_log_beats(
        self, params: Optional[LogBeatQueryParams] = None
    ) -> LogPage:
        return _inflate_log_page(
            await self._post(_BEAT_QUERY_PATH, params or LogBeatQueryParams()),
            "beat",
        )

    async def get_logs_count(self, params: Optional[LogQueryParams] = None) -> int:
        page = await self.get_logs(
            LogQueryParams(
                **{**(params.__dict__ if params else {}), "take": 0}
            ) if params else LogQueryParams(take=0)
        )
        return page.totalCount

    async def get_collections(self) -> List[str]:
        body = await self._get(_DISTINCT_COLLECTIONS_PATH)
        if isinstance(body, list):
            return [str(x) for x in body]
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            return [str(x) for x in body["items"]]
        return []

    async def get_event_log_status(self) -> EventLogStatus:
        body = await self._get(_EVENT_LOG_STATUS_PATH)
        if not isinstance(body, dict):
            return EventLogStatus()
        return EventLogStatus(
            hasWindowsEvents=bool(body.get("hasWindowsEvents", False)),
            hasIISEvents=bool(body.get("hasIISEvents", False)),
            hasWindowsMetrics=bool(body.get("hasWindowsMetrics", False)),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncLogDBReader":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    # ── Internals ──────────────────────────────────────────────────

    async def _post(self, path: str, params: object) -> Dict[str, Any]:
        url = (await self._resolver.resolve_async()) + path
        body = serialize_body(params)
        headers = build_headers(api_key=self._opts.api_key, extra=self._opts.headers)

        async def _do() -> httpx.Response:
            try:
                return await self._http.post(url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                raise translate_request_error(exc, url) from exc

        try:
            response = await call_with_retry_async(
                _do,
                max_retries=self._opts.max_retries,
                retry_delay=self._opts.retry_delay,
                retry_backoff_multiplier=self._opts.retry_backoff_multiplier,
            )
        except Exception as exc:
            if self._opts.on_error:
                try:
                    self._opts.on_error(exc, path)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: reader on_error callback itself failed")
            raise

        raise_for_status(response)
        data = response.json()
        return data if isinstance(data, dict) else {"items": data}

    async def _get(self, path: str) -> Any:
        url = (await self._resolver.resolve_async()) + path
        headers = build_headers(api_key=self._opts.api_key, extra=self._opts.headers)

        async def _do() -> httpx.Response:
            try:
                return await self._http.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise translate_request_error(exc, url) from exc

        try:
            response = await call_with_retry_async(
                _do,
                max_retries=self._opts.max_retries,
                retry_delay=self._opts.retry_delay,
                retry_backoff_multiplier=self._opts.retry_backoff_multiplier,
            )
        except Exception as exc:
            if self._opts.on_error:
                try:
                    self._opts.on_error(exc, path)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: reader on_error callback itself failed")
            raise
        raise_for_status(response)
        return response.json()
