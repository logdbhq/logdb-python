"""Drop-in :class:`logging.Handler` that forwards stdlib log records to
LogDB.

The vast majority of Python codebases (Django, Flask, scripts, CLIs,
workers) use stdlib :mod:`logging`. This handler is the one-line
adoption path:

.. code-block:: python

    import logging
    from logdbhq import LogDBClient
    from logdbhq.logging_handler import LogDBHandler

    client = LogDBClient(api_key="sk-...", default_application="my-django-app")
    logging.getLogger().addHandler(LogDBHandler(client))
    logging.info("hello")

The handler maps Python's numeric :class:`logging` levels to
:class:`~logdbhq.LogLevel`, pulls ``extra`` kwargs from the record into
typed attributes, and attaches any exception info to
:attr:`~logdbhq.Log.exception` / :attr:`~logdbhq.Log.stackTrace`.

**Threading + batching**: The handler enqueues into
:class:`~logdbhq.LogDBClient`'s batcher and returns immediately. No log
call blocks on the network. This is exactly the same behavior as the
underlying writer — the handler is a thin adapter, not a second layer
of buffering.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .client import AsyncLogDBClient, LogDBClient
from .models import Log, LogLevel


# Python stdlib severity → LogLevel. Off-stdlib levels (TRACE=5 if configured)
# still come through as whatever's closest; unknown just uses .value.
_LEVEL_MAP = {
    logging.DEBUG: LogLevel.Debug,
    logging.INFO: LogLevel.Info,
    logging.WARNING: LogLevel.Warning,
    logging.ERROR: LogLevel.Error,
    logging.CRITICAL: LogLevel.Critical,
}


# Attributes present on every LogRecord — we exclude these when pulling
# ``extra`` fields, since they're noise from LogDB's perspective.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _map_level(record_level: int) -> LogLevel:
    if record_level in _LEVEL_MAP:
        return _LEVEL_MAP[record_level]
    if record_level >= logging.CRITICAL:
        return LogLevel.Critical
    if record_level >= logging.ERROR:
        return LogLevel.Error
    if record_level >= logging.WARNING:
        return LogLevel.Warning
    if record_level >= logging.INFO:
        return LogLevel.Info
    return LogLevel.Debug


def _extra_from_record(record: logging.LogRecord) -> Dict[str, Any]:
    """Return just the user-provided ``extra`` kwargs — skip the noise
    stdlib adds to every record."""
    return {
        k: v
        for k, v in record.__dict__.items()
        if k not in _RESERVED_RECORD_ATTRS and not k.startswith("_")
    }


def _record_to_log(record: logging.LogRecord) -> Log:
    # Build a Log from a stdlib LogRecord without stamping defaults —
    # the client will do that on its own.
    log = Log(
        message=record.getMessage(),
        timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
        level=_map_level(record.levelno),
        source=record.name,
    )

    if record.exc_info:
        etype, evalue, etb = record.exc_info
        if etype is not None:
            log.exception = etype.__name__
        if etype is not None and evalue is not None:
            log.stackTrace = "".join(traceback.format_exception(etype, evalue, etb))

    # Route user-provided ``extra`` kwargs into typed attributes.
    extras = _extra_from_record(record)
    for key, value in extras.items():
        if isinstance(value, bool):
            log.attributesB = {**(log.attributesB or {}), key: value}
        elif isinstance(value, (int, float)):
            log.attributesN = {**(log.attributesN or {}), key: float(value)}
        elif isinstance(value, datetime):
            log.attributesD = {**(log.attributesD or {}), key: value}
        elif value is None:
            continue
        else:
            log.attributesS = {**(log.attributesS or {}), key: str(value)}

    return log


class LogDBHandler(logging.Handler):
    """Forwards stdlib :mod:`logging` records to a :class:`LogDBClient`.

    Works with both synchronous :class:`LogDBClient` and
    :class:`AsyncLogDBClient` — the async variant uses its batcher's
    non-blocking enqueue so the handler's :meth:`emit` remains
    synchronous (as stdlib logging requires)."""

    def __init__(
        self,
        client: "LogDBClient | AsyncLogDBClient",
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level=level)
        self._client = client
        # Track whether the client is async; we use its internal batcher
        # to enqueue synchronously (the batcher's enqueue IS sync even on
        # the AsyncLogDBClient because it's just a list append under a lock).
        self._is_async = isinstance(client, AsyncLogDBClient)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = _record_to_log(record)

            if self._is_async:
                # For the async client we enqueue directly into its batcher
                # to avoid needing a running event loop.
                client = self._client  # type: AsyncLogDBClient  # noqa: F841
                self._enqueue_async(log_entry)
            else:
                # Sync path — .log() is non-blocking when batching is on.
                self._client.log(log_entry)  # type: ignore[union-attr]
        except Exception:
            self.handleError(record)

    def _enqueue_async(self, log_entry: Log) -> None:
        # We can't ``await client.log(...)`` from a sync handler, but we
        # can poke the batcher directly — that's exactly what the
        # async client does under the hood (it doesn't await either).
        async_client: AsyncLogDBClient = self._client  # type: ignore[assignment]
        batcher = async_client._ensure_batcher()  # noqa: SLF001
        batcher.enqueue("log", log_entry)

    def close(self) -> None:
        """Does not close the underlying client — callers manage the client
        lifetime themselves. Flushing the handler just means forwarding has
        ceased; the client's own ``close()`` / ``flush()`` handle delivery
        guarantees at shutdown."""
        super().close()


__all__ = ["LogDBHandler"]
