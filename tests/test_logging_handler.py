"""stdlib logging adapter smoke tests."""

from __future__ import annotations

import json
import logging

from logdbhq import LogDBClient
from logdbhq.logging_handler import LogDBHandler


DUMMY_ENDPOINT = "https://test.example/rest-api"


def test_handler_forwards_info_record(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event", status_code=204)
    logger = logging.getLogger("test_handler_info")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    with LogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        default_application="t",
        enable_batching=False,
        max_retries=0,
        enable_circuit_breaker=False,
    ) as client:
        logger.addHandler(LogDBHandler(client))
        logger.info("ready", extra={"worker_id": 7, "healthy": True})

    request = httpx_mock.get_request()
    body = json.loads(request.content)
    assert body["message"] == "ready"
    assert body["level"] == "Info"
    assert body["source"] == "test_handler_info"
    # extras routed to typed attrs
    assert body["attributesN"] == {"worker_id": 7.0}
    assert body["attributesB"] == {"healthy": True}


def test_handler_captures_exception(httpx_mock):
    httpx_mock.add_response(url=f"{DUMMY_ENDPOINT}/log/event", status_code=204)
    logger = logging.getLogger("test_handler_exc")
    logger.handlers.clear()
    logger.setLevel(logging.ERROR)

    with LogDBClient(
        api_key="sk-test",
        endpoint=DUMMY_ENDPOINT,
        enable_batching=False,
        max_retries=0,
        enable_circuit_breaker=False,
    ) as client:
        logger.addHandler(LogDBHandler(client))
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("oops")

    request = httpx_mock.get_request()
    body = json.loads(request.content)
    assert body["message"] == "oops"
    assert body["exception"] == "ValueError"
    assert "ValueError: boom" in body["stackTrace"]
