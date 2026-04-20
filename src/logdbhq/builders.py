"""Fluent builders.

Python's dataclasses are nice enough that direct construction
(``Log(message="...", level=LogLevel.Info, ...)``) is usually cleaner
than a builder. But for progressively-enriched patterns (start from
context, add per-handler fields, log) or for parity with the .NET /
Node / PHP SDKs, builders are useful. Each method returns a new builder
instance — they're immutable by convention so sharing a base builder
across handlers is safe.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Any, Awaitable, Dict, List, Optional, Union

from .models import Log, LogBeat, LogCache, LogMeta, LogLevel, LogResponseStatus


# Forward-reference client types without importing (avoids a cycle).
_ClientLike = Any
_AsyncClientLike = Any


# ──────────────────────────────────────────────────────────────────────
# LogEventBuilder
# ──────────────────────────────────────────────────────────────────────


class LogEventBuilder:
    """Fluent builder for :class:`~logdbhq.Log`.

    All setters return a new builder so the same base can be forked
    across handlers without side-effects. Terminal :meth:`log` /
    :meth:`log_async` send through the bound client."""

    __slots__ = ("_log", "_client")

    def __init__(self, client: _ClientLike, log: Optional[Log] = None) -> None:
        self._client = client
        self._log = log if log is not None else Log()

    @classmethod
    def create(cls, client: _ClientLike) -> "LogEventBuilder":
        """Starts a fresh builder bound to ``client``."""
        return cls(client)

    # Internal helper for immutable-style transitions.
    def _with(self, **changes: Any) -> "LogEventBuilder":
        return LogEventBuilder(self._client, replace(self._log, **changes))

    def set_message(self, message: str) -> "LogEventBuilder":
        return self._with(message=message)

    def set_log_level(self, level: LogLevel) -> "LogEventBuilder":
        return self._with(level=level)

    def set_timestamp(self, timestamp: datetime) -> "LogEventBuilder":
        return self._with(timestamp=timestamp)

    def set_application(self, application: str) -> "LogEventBuilder":
        return self._with(application=application)

    def set_environment(self, environment: str) -> "LogEventBuilder":
        return self._with(environment=environment)

    def set_collection(self, collection: str) -> "LogEventBuilder":
        return self._with(collection=collection)

    def set_correlation_id(self, correlation_id: str) -> "LogEventBuilder":
        return self._with(correlationId=correlation_id)

    def set_user_email(self, email: str) -> "LogEventBuilder":
        return self._with(userEmail=email)

    def set_user_id(self, user_id: int) -> "LogEventBuilder":
        return self._with(userId=user_id)

    def set_request_path(self, path: str) -> "LogEventBuilder":
        return self._with(requestPath=path)

    def set_http_method(self, method: str) -> "LogEventBuilder":
        return self._with(httpMethod=method)

    def set_status_code(self, status: int) -> "LogEventBuilder":
        return self._with(statusCode=status)

    def set_ip_address(self, ip: str) -> "LogEventBuilder":
        return self._with(ipAddress=ip)

    def set_exception(self, exc: BaseException) -> "LogEventBuilder":
        """Fills :attr:`Log.exception` and :attr:`Log.stackTrace` from a
        live exception. Also sets :attr:`Log.level` to :attr:`LogLevel.Exception`
        if no level has been set explicitly yet."""
        import traceback

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        next_level = self._log.level if self._log.level is not None else LogLevel.Exception
        return self._with(
            exception=type(exc).__name__,
            stackTrace=tb,
            level=next_level,
        )

    def set_source(self, source: str) -> "LogEventBuilder":
        return self._with(source=source)

    def set_description(self, description: str) -> "LogEventBuilder":
        return self._with(description=description)

    def set_additional_data(self, data: str) -> "LogEventBuilder":
        return self._with(additionalData=data)

    def add_label(self, label: str) -> "LogEventBuilder":
        labels = list(self._log.label or [])
        labels.append(label)
        return self._with(label=labels)

    def add_labels(self, labels: List[str]) -> "LogEventBuilder":
        existing = list(self._log.label or [])
        existing.extend(labels)
        return self._with(label=existing)

    def add_attribute(
        self,
        key: str,
        value: Union[str, int, float, bool, datetime],
    ) -> "LogEventBuilder":
        """Dispatch into the right typed attributes map based on Python type.
        Matches the other SDKs' ``addAttribute`` overloads — the value type
        determines the bucket."""
        # Important: ``bool`` is a subclass of ``int``, so check it first.
        if isinstance(value, bool):
            attrs_b = dict(self._log.attributesB or {})
            attrs_b[key] = value
            return self._with(attributesB=attrs_b)
        if isinstance(value, (int, float)):
            attrs_n = dict(self._log.attributesN or {})
            attrs_n[key] = float(value)
            return self._with(attributesN=attrs_n)
        if isinstance(value, datetime):
            attrs_d = dict(self._log.attributesD or {})
            attrs_d[key] = value
            return self._with(attributesD=attrs_d)
        # Fallback: string
        attrs_s = dict(self._log.attributesS or {})
        attrs_s[key] = str(value)
        return self._with(attributesS=attrs_s)

    # Terminal operations ──────────────────────────────────────────

    def build(self) -> Log:
        """Return the accumulated :class:`Log` without sending. Useful for
        tests or if you want to hand the object to ``send_log_batch``."""
        return deepcopy(self._log)

    def log(self) -> LogResponseStatus:
        """Synchronously dispatch through the bound client."""
        return self._client.log(self.build())

    async def log_async(self) -> LogResponseStatus:
        """Awaitable dispatch — use when bound to an :class:`AsyncLogDBClient`."""
        return await self._client.log(self.build())


# ──────────────────────────────────────────────────────────────────────
# LogBeatBuilder
# ──────────────────────────────────────────────────────────────────────


class LogBeatBuilder:
    """Fluent builder for :class:`~logdbhq.LogBeat`."""

    __slots__ = ("_beat", "_client")

    def __init__(self, client: _ClientLike, beat: Optional[LogBeat] = None) -> None:
        self._client = client
        self._beat = beat if beat is not None else LogBeat()

    @classmethod
    def create(cls, client: _ClientLike) -> "LogBeatBuilder":
        return cls(client)

    def _with(self, **changes: Any) -> "LogBeatBuilder":
        return LogBeatBuilder(self._client, replace(self._beat, **changes))

    def set_measurement(self, measurement: str) -> "LogBeatBuilder":
        return self._with(measurement=measurement)

    def set_timestamp(self, timestamp: datetime) -> "LogBeatBuilder":
        return self._with(timestamp=timestamp)

    def set_collection(self, collection: str) -> "LogBeatBuilder":
        return self._with(collection=collection)

    def set_environment(self, environment: str) -> "LogBeatBuilder":
        return self._with(environment=environment)

    def add_tag(self, key: str, value: str) -> "LogBeatBuilder":
        tags = list(self._beat.tag or [])
        tags.append(LogMeta(key=key, value=str(value)))
        return self._with(tag=tags)

    def add_field(self, key: str, value: Union[str, int, float, bool]) -> "LogBeatBuilder":
        fields = list(self._beat.field or [])
        fields.append(LogMeta(key=key, value=str(value)))
        return self._with(field=fields)

    def build(self) -> LogBeat:
        return deepcopy(self._beat)

    def log(self) -> LogResponseStatus:
        return self._client.log_beat(self.build())

    async def log_async(self) -> LogResponseStatus:
        return await self._client.log_beat(self.build())


# ──────────────────────────────────────────────────────────────────────
# LogCacheBuilder
# ──────────────────────────────────────────────────────────────────────


class LogCacheBuilder:
    """Fluent builder for :class:`~logdbhq.LogCache`.

    Accepts any JSON-serializable value via :meth:`set_value`; it's
    :func:`json.dumps`-ed to a string, since the cache column is string-typed.
    """

    __slots__ = ("_cache", "_client")

    def __init__(self, client: _ClientLike, cache: Optional[LogCache] = None) -> None:
        self._client = client
        self._cache = cache if cache is not None else LogCache()

    @classmethod
    def create(cls, client: _ClientLike) -> "LogCacheBuilder":
        return cls(client)

    def _with(self, **changes: Any) -> "LogCacheBuilder":
        return LogCacheBuilder(self._client, replace(self._cache, **changes))

    def set_key(self, key: str) -> "LogCacheBuilder":
        return self._with(key=key)

    def set_value(self, value: Any) -> "LogCacheBuilder":
        import json

        if isinstance(value, str):
            return self._with(value=value)
        return self._with(value=json.dumps(value, default=str, separators=(",", ":")))

    def set_ttl_seconds(self, ttl: int) -> "LogCacheBuilder":
        return self._with(ttlSeconds=ttl)

    def build(self) -> LogCache:
        return deepcopy(self._cache)

    def log(self) -> LogResponseStatus:
        return self._client.log_cache(self.build())

    async def log_async(self) -> LogResponseStatus:
        return await self._client.log_cache(self.build())
