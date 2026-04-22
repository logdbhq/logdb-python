"""Client-level integration tests using :mod:`pytest_httpx` to mock the
HTTP transport. Exercises the full stack (serialization, discovery,
resilience, batching) without touching the network."""

from __future__ import annotations

import asyncio
import json

import pytest

from logdbhq import (
    AsyncLogDBClient,
    Log,
    LogDBAuthError,
    LogDBClient,
    LogLevel,
    LogResponseStatus,
)


DUMMY_ENDPOINT = "https://test.example/rest-api"


# ── sync ─────────────────────────────────────────────────────────────


def test_direct_send_hits_single_endpoint(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event", status_code=204)
    with LogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        default_application="test-app",
        enable_batching=False,
        max_retries=0,
        enable_circuit_breaker=False,
    ) as client:
        status = client.log(Log(message="hi", level=LogLevel.Info))
    assert status == LogResponseStatus.Success

    request = httpx_mock.get_request()
    body = json.loads(request.content)
    assert body["message"] == "hi"
    assert body["level"] == "Info"
    assert body["application"] == "test-app"  # default stamped
    assert body["environment"] == "production"  # default stamped
    assert request.headers["X-LogDB-ApiKey"] == "sk-test"


def test_direct_batch_hits_batch_endpoint(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event/batch", status_code=204)
    with LogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        enable_batching=False,
        max_retries=0,
    ) as client:
        status = client.send_log_batch([Log(message="a"), Log(message="b")])
    assert status == LogResponseStatus.Success

    request = httpx_mock.get_request()
    body = json.loads(request.content)
    assert isinstance(body, list)
    assert [x["message"] for x in body] == ["a", "b"]


def test_401_returns_not_authorized_status(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event", status_code=401, text="bad key")
    with LogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        enable_batching=False,
        max_retries=0,
        enable_circuit_breaker=False,
    ) as client:
        status = client.log(Log(message="hi"))
    assert status == LogResponseStatus.NotAuthorized


def test_on_error_receives_exception_and_batch(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event", status_code=401)
    captured = {}

    def on_error(exc, batch):
        captured["exc"] = exc
        captured["batch"] = batch

    with LogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        enable_batching=False,
        max_retries=0,
        enable_circuit_breaker=False,
        on_error=on_error,
    ) as client:
        client.log(Log(message="hi"))
    assert isinstance(captured["exc"], LogDBAuthError)
    assert len(captured["batch"]) == 1
    assert captured["batch"][0].message == "hi"


def test_batching_flushes_on_close(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event/batch", status_code=204)
    with LogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        enable_batching=True,
        batch_size=100,  # never hit by size
        flush_interval=60.0,  # never hit by time
        max_retries=0,
        enable_circuit_breaker=False,
    ) as client:
        # enqueue enough for a list-shaped batch (>= 2 so goes through /batch path)
        client.log(Log(message="a"))
        client.log(Log(message="b"))
        # close() triggers drain
    request = httpx_mock.get_request()
    assert request is not None
    body = json.loads(request.content)
    assert isinstance(body, list)
    assert {x["message"] for x in body} == {"a", "b"}


# ── async ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_direct_send_hits_single_endpoint(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event", status_code=204)
    async with AsyncLogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        default_application="test-app",
        enable_batching=False,
        max_retries=0,
        enable_circuit_breaker=False,
    ) as client:
        status = await client.log(Log(message="async", level=LogLevel.Info))
    assert status == LogResponseStatus.Success


@pytest.mark.asyncio
async def test_async_batch(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event/batch", status_code=204)
    async with AsyncLogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        enable_batching=False,
        max_retries=0,
    ) as client:
        status = await client.send_log_batch([Log(message="a"), Log(message="b")])
    assert status == LogResponseStatus.Success
