"""Retry policy + circuit breaker.

Both mirror the `.NET`, Node, and PHP SDKs so behavior is consistent
across runtimes. Pulled out of the client so they're unit-testable in
isolation without spinning up httpx or a server.

**Retry policy**
  Exponential backoff with ±20% jitter. Only retries transient errors
  (:class:`~logdbhq.LogDBNetworkError`, :class:`~logdbhq.LogDBTimeoutError`).
  Auth and config errors fail fast.

**Circuit breaker**
  Sliding-window failure-rate breaker. Every outcome goes into a
  ring buffer keyed by time. When the failure rate within the
  window crosses the threshold, the breaker opens: ``should_allow``
  returns ``False`` and the caller raises
  :class:`~logdbhq.LogDBCircuitOpenError`. After
  ``duration_of_break`` elapses, the breaker transitions to half-open
  and probes with the next real request — success closes, failure
  reopens.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections import deque
from enum import Enum
from typing import Awaitable, Callable, Deque, TypeVar

from .errors import LogDBAuthError, LogDBConfigError, LogDBError


T = TypeVar("T")


def _is_retryable(exc: BaseException) -> bool:
    """Auth + config failures fail fast; everything else (network, timeout,
    generic LogDBError) is transient."""
    if isinstance(exc, (LogDBAuthError, LogDBConfigError)):
        return False
    return isinstance(exc, LogDBError) or isinstance(exc, Exception)


def _compute_delay(attempt: int, base: float, multiplier: float) -> float:
    """Exponential backoff with ±20% jitter.

    ``attempt`` is zero-indexed for the *next* delay to sleep: attempt 0
    → ``base``, attempt 1 → ``base * multiplier``, attempt 2 →
    ``base * multiplier**2``, etc.
    """
    delay = base * (multiplier ** attempt)
    jitter = random.uniform(-0.2, 0.2) * delay
    return max(0.0, delay + jitter)


def call_with_retry_sync(
    fn: Callable[[], T],
    *,
    max_retries: int,
    retry_delay: float,
    retry_backoff_multiplier: float,
) -> T:
    last: BaseException
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except BaseException as exc:
            last = exc
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            time.sleep(_compute_delay(attempt, retry_delay, retry_backoff_multiplier))
    # Unreachable — the loop either returns or re-raises.
    raise last  # pragma: no cover


async def call_with_retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    retry_delay: float,
    retry_backoff_multiplier: float,
) -> T:
    last: BaseException
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except BaseException as exc:
            last = exc
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            await asyncio.sleep(
                _compute_delay(attempt, retry_delay, retry_backoff_multiplier)
            )
    raise last  # pragma: no cover


# ──────────────────────────────────────────────────────────────────────
# Circuit breaker
# ──────────────────────────────────────────────────────────────────────


class CircuitState(str, Enum):
    Closed = "closed"
    Open = "open"
    HalfOpen = "half-open"


class CircuitBreaker:
    """Thread-safe sliding-window failure-rate breaker.

    Call :meth:`should_allow` before making a request. If it returns
    ``False``, short-circuit with a
    :class:`~logdbhq.LogDBCircuitOpenError`. After the call, invoke
    :meth:`record_success` or :meth:`record_failure`.
    """

    def __init__(
        self,
        *,
        failure_threshold: float = 0.5,
        sampling_duration: float = 10.0,
        duration_of_break: float = 30.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._sampling_duration = sampling_duration
        self._duration_of_break = duration_of_break

        self._state: CircuitState = CircuitState.Closed
        self._opened_at: float = 0.0
        self._samples: Deque[tuple[float, bool]] = deque()  # (timestamp, succeeded)
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def should_allow(self) -> bool:
        with self._lock:
            if self._state == CircuitState.Closed:
                return True
            if self._state == CircuitState.Open:
                if time.monotonic() - self._opened_at >= self._duration_of_break:
                    self._state = CircuitState.HalfOpen
                    return True
                return False
            # half-open → let exactly one probe through. Subsequent calls
            # wait for the probe's outcome before they're allowed.
            return True

    def record_success(self) -> None:
        with self._lock:
            self._samples.append((time.monotonic(), True))
            self._prune_locked()
            if self._state != CircuitState.Closed:
                self._state = CircuitState.Closed
                self._samples.clear()

    def record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._samples.append((now, False))
            self._prune_locked()

            # Half-open probe failed → reopen immediately.
            if self._state == CircuitState.HalfOpen:
                self._state = CircuitState.Open
                self._opened_at = now
                return

            if self._state == CircuitState.Closed:
                # Need at least a few samples to avoid tripping on a single fail.
                if len(self._samples) >= 4 and self._failure_rate_locked() >= self._failure_threshold:
                    self._state = CircuitState.Open
                    self._opened_at = now

    def _prune_locked(self) -> None:
        cutoff = time.monotonic() - self._sampling_duration
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _failure_rate_locked(self) -> float:
        if not self._samples:
            return 0.0
        failures = sum(1 for _, ok in self._samples if not ok)
        return failures / len(self._samples)
