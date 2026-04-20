"""LogDB SDK for Python.

The public surface is re-exported here so users can import from the
top-level package instead of reaching into submodules::

    from logdbhq import LogDBClient, Log, LogLevel

Submodules (e.g. ``logdbhq.logging_handler`` for the stdlib logging
integration) are imported on demand — they're not re-exported at the
top level to avoid pulling in :mod:`logging` for apps that don't use it.
"""

from ._version import __version__
from .client import AsyncLogDBClient, LogDBClient, logdb_client
from .errors import (
    LogDBAuthError,
    LogDBCircuitOpenError,
    LogDBConfigError,
    LogDBError,
    LogDBNetworkError,
    LogDBTimeoutError,
)
from .models import (
    EventLogStatus,
    Log,
    LogBeat,
    LogBeatEntry,
    LogCache,
    LogCacheEntry,
    LogEntry,
    LogLevel,
    LogMeta,
    LogPage,
    LogResponseStatus,
)
from .options import LogDBClientOptions, LogDBReaderOptions
from .reader import (
    AsyncLogDBReader,
    BaseQueryParams,
    LogBeatQueryParams,
    LogCacheQueryParams,
    LogDBReader,
    LogQueryParams,
)
from .builders import LogBeatBuilder, LogCacheBuilder, LogEventBuilder

__all__ = [
    # Clients
    "LogDBClient",
    "AsyncLogDBClient",
    "logdb_client",
    # Readers
    "LogDBReader",
    "AsyncLogDBReader",
    # Options
    "LogDBClientOptions",
    "LogDBReaderOptions",
    # Models
    "Log",
    "LogBeat",
    "LogCache",
    "LogMeta",
    "LogLevel",
    "LogResponseStatus",
    "LogEntry",
    "LogBeatEntry",
    "LogCacheEntry",
    "LogPage",
    "EventLogStatus",
    # Query params
    "LogQueryParams",
    "LogBeatQueryParams",
    "LogCacheQueryParams",
    "BaseQueryParams",
    # Builders
    "LogEventBuilder",
    "LogBeatBuilder",
    "LogCacheBuilder",
    # Errors
    "LogDBError",
    "LogDBAuthError",
    "LogDBConfigError",
    "LogDBNetworkError",
    "LogDBTimeoutError",
    "LogDBCircuitOpenError",
    # Version
    "__version__",
]
