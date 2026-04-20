"""Retry + circuit breaker unit tests."""

from __future__ import annotations

import asyncio
import time

import pytest

from logdbhq.errors import LogDBAuthError, LogDBNetworkError
from logdbhq.resilience import (
    CircuitBreaker,
    CircuitState,
    call_with_retry_async,
    call_with_retry_sync,
)


# ── retry ────────────────────────────────────────────────────────────


def test_sync_retry_eventually_succeeds():
    attempts = {"count": 0}

    def fn():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise LogDBNetworkError("transient")
        return "ok"

    result = call_with_retry_sync(fn, max_retries=3, retry_delay=0.01, retry_backoff_multiplier=1.5)
    assert result == "ok"
    assert attempts["count"] == 3


def test_sync_retry_gives_up_after_max():
    attempts = {"count": 0}

    def fn():
        attempts["count"] += 1
        raise LogDBNetworkError("always fails")

    with pytest.raises(LogDBNetworkError):
        call_with_retry_sync(fn, max_retries=2, retry_delay=0.01, retry_backoff_multiplier=1.0)
    assert attempts["count"] == 3  # initial + 2 retries


def test_sync_retry_does_not_retry_auth_errors():
    attempts = {"count": 0}

    def fn():
        attempts["count"] += 1
        raise LogDBAuthError("bad key")

    with pytest.raises(LogDBAuthError):
        call_with_retry_sync(fn, max_retries=5, retry_delay=0.01, retry_backoff_multiplier=1.0)
    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_async_retry_eventually_succeeds():
    attempts = {"count": 0}

    async def fn():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise LogDBNetworkError("transient")
        return "ok"

    result = await call_with_retry_async(
        fn, max_retries=3, retry_delay=0.01, retry_backoff_multiplier=1.5
    )
    assert result == "ok"
    assert attempts["count"] == 2


# ── circuit breaker ─────────────────────────────────────────────────


def test_breaker_opens_after_failures_and_blocks():
    cb = CircuitBreaker(failure_threshold=0.5, sampling_duration=10, duration_of_break=0.05)
    # Feed 4 failures in a row — should trip (≥50% failure rate over ≥4 samples).
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CircuitState.Open
    assert cb.should_allow() is False


def test_breaker_recovers_after_duration():
    cb = CircuitBreaker(failure_threshold=0.5, sampling_duration=10, duration_of_break=0.05)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CircuitState.Open
    time.sleep(0.06)
    # Next call transitions to half-open.
    assert cb.should_allow() is True
    cb.record_success()
    assert cb.state == CircuitState.Closed


def test_breaker_half_open_failure_reopens():
    cb = CircuitBreaker(failure_threshold=0.5, sampling_duration=10, duration_of_break=0.05)
    for _ in range(4):
        cb.record_failure()
    time.sleep(0.06)
    cb.should_allow()  # half-open
    cb.record_failure()
    assert cb.state == CircuitState.Open


def test_breaker_stays_closed_on_success_stream():
    cb = CircuitBreaker()
    for _ in range(10):
        cb.record_success()
    assert cb.state == CircuitState.Closed
    assert cb.should_allow()
