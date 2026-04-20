"""Write-side domain models (:class:`Log`, :class:`LogBeat`, :class:`LogCache`)
and shared enums (:class:`LogLevel`, :class:`LogResponseStatus`).

All models are plain :mod:`dataclasses` with optional fields. Field names
match the server's JSON wire shape exactly (camelCase) so serialization
is a straight :func:`~dataclasses.asdict`; Python-idiomatic snake_case
aliases are exposed on builders, not here, to keep models faithful to the
wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class LogLevel(int, Enum):
    """Severity levels as understood by LogDB. Matches the `.NET`, Node,
    and PHP SDKs exactly so a log emitted from one and queried from
    another round-trips cleanly."""

    Trace = 0
    Debug = 1
    Info = 2
    Warning = 3
    Error = 4
    Critical = 5
    Exception = 6


class LogResponseStatus(str, Enum):
    """Returned from writer methods. Non-throwing classification."""

    Success = "Success"
    """Accepted by the server."""

    Failed = "Failed"
    """Server returned a non-success response (after retries)."""

    NotAuthorized = "NotAuthorized"
    """401 / 403 — check API key."""

    CircuitOpen = "CircuitOpen"
    """Short-circuited locally because the breaker was open."""

    Timeout = "Timeout"
    """Per-request deadline exceeded."""


@dataclass
class Log:
    """A single log event. Only :attr:`message` is required; everything else
    is optional and may be filled by the server (IDs, timestamps) or by
    client-side defaults (:attr:`application`, :attr:`environment`,
    :attr:`collection`)."""

    message: str = ""
    timestamp: Optional[datetime] = None
    level: Optional[LogLevel] = None
    application: Optional[str] = None
    environment: Optional[str] = None
    collection: Optional[str] = None
    exception: Optional[str] = None
    stackTrace: Optional[str] = None
    source: Optional[str] = None
    userId: Optional[int] = None
    userEmail: Optional[str] = None
    correlationId: Optional[str] = None
    requestPath: Optional[str] = None
    httpMethod: Optional[str] = None
    additionalData: Optional[str] = None
    ipAddress: Optional[str] = None
    statusCode: Optional[int] = None
    description: Optional[str] = None
    id: Optional[int] = None
    guid: Optional[str] = None
    label: Optional[List[str]] = None
    attributesS: Optional[Dict[str, str]] = None
    attributesN: Optional[Dict[str, float]] = None
    attributesB: Optional[Dict[str, bool]] = None
    attributesD: Optional[Dict[str, datetime]] = None
    apiKey: Optional[str] = None


@dataclass
class LogMeta:
    """A single ``{key, value}`` pair used for LogBeat tags and fields."""

    key: str = ""
    value: str = ""


@dataclass
class LogBeat:
    """A metric / heartbeat measurement. Tags identify the series, fields
    carry the payload. The shape mirrors InfluxDB line-protocol on
    purpose — it's the pattern most observability pipelines already grok."""

    measurement: str = ""
    tag: Optional[List[LogMeta]] = None
    field: Optional[List[LogMeta]] = None
    timestamp: Optional[datetime] = None
    collection: Optional[str] = None
    environment: Optional[str] = None
    guid: Optional[str] = None
    apiKey: Optional[str] = None


@dataclass
class LogCache:
    """Key/value write. Values are arbitrary strings; if you want
    structured data, serialize yourself (JSON, protobuf, whatever)."""

    key: str = ""
    value: str = ""
    guid: Optional[str] = None
    apiKey: Optional[str] = None
    ttlSeconds: Optional[int] = None
    """Reserved for server-side TTL. Ignored by the current server but
    part of the wire contract so it's here for forward-compat."""


# ──────────────────────────────────────────────────────────────────────
# Reader-side entry / page types
# ──────────────────────────────────────────────────────────────────────


@dataclass
class LogEntry:
    """A materialized log row as returned by :meth:`LogDBReader.get_logs`."""

    id: Optional[int] = None
    guid: Optional[str] = None
    timestamp: Optional[datetime] = None
    application: Optional[str] = None
    environment: Optional[str] = None
    level: Optional[str] = None
    message: Optional[str] = None
    exception: Optional[str] = None
    stackTrace: Optional[str] = None
    source: Optional[str] = None
    userId: Optional[int] = None
    userEmail: Optional[str] = None
    correlationId: Optional[str] = None
    requestPath: Optional[str] = None
    httpMethod: Optional[str] = None
    additionalData: Optional[str] = None
    ipAddress: Optional[str] = None
    statusCode: Optional[int] = None
    description: Optional[str] = None
    collection: Optional[str] = None
    labels: Optional[List[str]] = None
    attributesS: Optional[Dict[str, str]] = None
    attributesN: Optional[Dict[str, float]] = None
    attributesB: Optional[Dict[str, bool]] = None
    attributesD: Optional[Dict[str, datetime]] = None
    raw: Optional[Dict[str, Any]] = None
    """Full server payload when the row carries fields this client
    version doesn't yet model. Use this as an escape hatch to read
    newly-added columns without upgrading the SDK."""


@dataclass
class LogBeatEntry:
    """One beat row from :meth:`LogDBReader.get_log_beats`."""

    id: Optional[int] = None
    guid: Optional[str] = None
    timestamp: Optional[datetime] = None
    measurement: Optional[str] = None
    tags: Optional[Dict[str, str]] = None
    fields: Optional[Dict[str, Any]] = None
    collection: Optional[str] = None
    environment: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class LogCacheEntry:
    """One cache row from :meth:`LogDBReader.get_log_caches`."""

    id: Optional[int] = None
    guid: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None
    collection: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class LogPage:
    """Generic paging envelope shared by the three read methods.

    Use :attr:`items` for the materialized rows and :attr:`total_count`
    to drive pagination UIs. :attr:`has_more` is a convenience derived
    server-side from ``skip + items.length < total_count``."""

    items: List[Any] = field(default_factory=list)
    totalCount: int = 0
    page: int = 0
    pageSize: int = 0
    hasMore: bool = False


@dataclass
class EventLogStatus:
    """Feature-availability flags for a LogDB tenant. Returned from
    :meth:`LogDBReader.get_event_log_status`."""

    hasWindowsEvents: bool = False
    hasIISEvents: bool = False
    hasWindowsMetrics: bool = False
