"""Batch engines — one per concurrency model.

The client never enqueues a single HTTP request per log; it buffers per
type (log / beat / cache) and flushes when either:

* ``batch_size`` entries have accumulated for that type, or
* ``flush_interval`` seconds have elapsed since the oldest buffered
  entry for that type, or
* ``flush()`` / ``dispose()`` was called explicitly.

Two implementations so sync and async clients share no locks / loops:

* :class:`SyncBatcher` runs a background :class:`threading.Thread`
* :class:`AsyncBatcher` runs an :mod:`asyncio` task

Both call back into a user-provided ``send_batch(type, items)`` callable
that does the actual HTTP work. That callable is expected to block (sync)
or await (async) until the request completes — retry and circuit-breaker
wrapping happen *inside* the callable, not here.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List

from .errors import LogDBError


_log = logging.getLogger("logdbhq.batching")


# ──────────────────────────────────────────────────────────────────────
# Sync
# ──────────────────────────────────────────────────────────────────────


class SyncBatcher:
    """Thread-safe buffer-with-timer.

    ``send_batch`` must be safe to call from a background thread.
    """

    def __init__(
        self,
        *,
        send_batch: Callable[[str, List[Any]], None],
        batch_size: int,
        flush_interval: float,
        on_error: Callable[[Exception, List[Any]], None] | None = None,
    ) -> None:
        self._send = send_batch
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._on_error = on_error

        self._buffers: Dict[str, List[Any]] = defaultdict(list)
        self._oldest: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False

        self._thread = threading.Thread(
            target=self._run, name="logdbhq-batcher", daemon=True
        )
        self._thread.start()

    def enqueue(self, kind: str, item: Any) -> None:
        size_hit: bool
        with self._lock:
            if self._closed:
                raise RuntimeError("SyncBatcher: enqueue after close")
            buf = self._buffers[kind]
            buf.append(item)
            self._oldest.setdefault(kind, time.monotonic())
            size_hit = len(buf) >= self._batch_size
        if size_hit:
            self._wake.set()

    def flush(self) -> None:
        """Force-flush all buffers synchronously, blocking until sends complete."""
        self._drain_all()

    def close(self) -> None:
        """Flush and stop the background thread. Safe to call more than once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._wake.set()
        self._thread.join(timeout=max(self._flush_interval, 5.0))
        self._drain_all()

    def _run(self) -> None:
        while True:
            # Wake on: size-triggered flush, close, or the timer.
            self._wake.wait(timeout=max(0.1, self._flush_interval / 2))
            self._wake.clear()

            self._flush_due()

            with self._lock:
                done = self._closed
            if done:
                break

    def _flush_due(self) -> None:
        """Flush only the types whose buffers are over threshold or timed out."""
        now = time.monotonic()
        to_send: List[tuple[str, List[Any]]] = []
        with self._lock:
            for kind, buf in list(self._buffers.items()):
                if not buf:
                    continue
                oldest = self._oldest.get(kind, now)
                if len(buf) >= self._batch_size or (now - oldest) >= self._flush_interval:
                    to_send.append((kind, buf))
                    self._buffers[kind] = []
                    self._oldest.pop(kind, None)
        for kind, items in to_send:
            self._safe_send(kind, items)

    def _drain_all(self) -> None:
        to_send: List[tuple[str, List[Any]]] = []
        with self._lock:
            for kind, buf in list(self._buffers.items()):
                if buf:
                    to_send.append((kind, buf))
            self._buffers.clear()
            self._oldest.clear()
        for kind, items in to_send:
            self._safe_send(kind, items)

    def _safe_send(self, kind: str, items: List[Any]) -> None:
        try:
            self._send(kind, items)
        except LogDBError as exc:
            _log.warning("logdbhq: batch %s failed with %d items: %s", kind, len(items), exc)
            if self._on_error:
                try:
                    self._on_error(exc, items)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: on_error callback itself failed")
        except Exception as exc:  # noqa: BLE001
            _log.exception("logdbhq: unexpected error in batch send")
            if self._on_error:
                try:
                    self._on_error(exc, items)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: on_error callback itself failed")


# ──────────────────────────────────────────────────────────────────────
# Async
# ──────────────────────────────────────────────────────────────────────


class AsyncBatcher:
    """:mod:`asyncio` equivalent of :class:`SyncBatcher`.

    Instantiated lazily by :class:`~logdbhq.AsyncLogDBClient` on first
    enqueue so the client's constructor can stay sync and bind to the
    caller's running loop automatically."""

    def __init__(
        self,
        *,
        send_batch: Callable[[str, List[Any]], Awaitable[None]],
        batch_size: int,
        flush_interval: float,
        on_error: Callable[[Exception, List[Any]], None] | None = None,
    ) -> None:
        self._send = send_batch
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._on_error = on_error

        self._buffers: Dict[str, List[Any]] = defaultdict(list)
        self._oldest: Dict[str, float] = {}
        self._wake = asyncio.Event()
        self._closed = False
        self._task: asyncio.Task[None] = asyncio.get_event_loop().create_task(
            self._run()
        )

    def enqueue(self, kind: str, item: Any) -> None:
        if self._closed:
            raise RuntimeError("AsyncBatcher: enqueue after close")
        buf = self._buffers[kind]
        buf.append(item)
        self._oldest.setdefault(kind, time.monotonic())
        if len(buf) >= self._batch_size:
            self._wake.set()

    async def flush(self) -> None:
        await self._drain_all()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wake.set()
        try:
            await asyncio.wait_for(self._task, timeout=max(self._flush_interval, 5.0))
        except asyncio.TimeoutError:
            self._task.cancel()
        await self._drain_all()

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=max(0.1, self._flush_interval / 2)
                )
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

            await self._flush_due()

            if self._closed:
                break

    async def _flush_due(self) -> None:
        now = time.monotonic()
        to_send: List[tuple[str, List[Any]]] = []
        for kind, buf in list(self._buffers.items()):
            if not buf:
                continue
            oldest = self._oldest.get(kind, now)
            if len(buf) >= self._batch_size or (now - oldest) >= self._flush_interval:
                to_send.append((kind, buf))
                self._buffers[kind] = []
                self._oldest.pop(kind, None)
        for kind, items in to_send:
            await self._safe_send(kind, items)

    async def _drain_all(self) -> None:
        to_send: List[tuple[str, List[Any]]] = []
        for kind, buf in list(self._buffers.items()):
            if buf:
                to_send.append((kind, buf))
        self._buffers.clear()
        self._oldest.clear()
        for kind, items in to_send:
            await self._safe_send(kind, items)

    async def _safe_send(self, kind: str, items: List[Any]) -> None:
        try:
            await self._send(kind, items)
        except LogDBError as exc:
            _log.warning("logdbhq: batch %s failed with %d items: %s", kind, len(items), exc)
            if self._on_error:
                try:
                    self._on_error(exc, items)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: on_error callback itself failed")
        except Exception as exc:  # noqa: BLE001
            _log.exception("logdbhq: unexpected error in async batch send")
            if self._on_error:
                try:
                    self._on_error(exc, items)
                except Exception:  # noqa: BLE001
                    _log.exception("logdbhq: on_error callback itself failed")
