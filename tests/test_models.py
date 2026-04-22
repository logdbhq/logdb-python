"""Data model + serialization tests. No network, no httpx.

Verifies the JSON wire format is what the backend expects (camelCase,
ISO-8601 UTC timestamps, ``None`` values dropped).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from logdbhq import Log, LogBeat, LogCache, LogLevel, LogMeta
from logdbhq._transport import serialize_body


def test_log_minimum_serializes_to_just_message():
    body = serialize_body(Log(message="hi"))
    assert json.loads(body) == {"message": "hi"}


def test_log_with_level_and_attributes():
    log = Log(
        message="payment",
        level=LogLevel.Info,
        userEmail="alice@example.com",
        attributesS={"currency": "EUR"},
        attributesN={"amount_eur": 199.99},
        attributesB={"fraud_reviewed": True},
        label=["payment", "checkout"],
    )
    payload = json.loads(serialize_body(log))
    assert payload["message"] == "payment"
    assert payload["level"] == "Info"  # wire-format string name, not int
    assert payload["userEmail"] == "alice@example.com"
    assert payload["attributesS"] == {"currency": "EUR"}
    assert payload["attributesN"] == {"amount_eur": 199.99}
    assert payload["attributesB"] == {"fraud_reviewed": True}
    assert payload["label"] == ["payment", "checkout"]


def test_datetime_serializes_as_iso8601_z():
    t = datetime(2026, 4, 20, 15, 30, 0, tzinfo=timezone.utc)
    body = json.loads(serialize_body(Log(message="t", timestamp=t)))
    assert body["timestamp"] == "2026-04-20T15:30:00Z"


def test_naive_datetime_treated_as_utc():
    t = datetime(2026, 4, 20, 15, 30, 0)  # no tzinfo
    body = json.loads(serialize_body(Log(message="t", timestamp=t)))
    assert body["timestamp"].endswith("Z")


def test_none_fields_stripped():
    log = Log(message="hi")  # every other field None
    body = json.loads(serialize_body(log))
    assert "exception" not in body
    assert "stackTrace" not in body
    assert "userId" not in body


def test_log_beat_serializes_with_meta_lists():
    beat = LogBeat(
        measurement="cpu",
        tag=[LogMeta(key="host", value="web-01"), LogMeta(key="region", value="eu-west-1")],
        field=[LogMeta(key="usage", value="78.5")],
    )
    payload = json.loads(serialize_body(beat))
    assert payload["measurement"] == "cpu"
    assert payload["tag"] == [
        {"key": "host", "value": "web-01"},
        {"key": "region", "value": "eu-west-1"},
    ]
    assert payload["field"] == [{"key": "usage", "value": "78.5"}]


def test_log_cache_round_trip():
    cache = LogCache(key="user:42", value='{"name":"Alice"}')
    payload = json.loads(serialize_body(cache))
    assert payload == {"key": "user:42", "value": '{"name":"Alice"}'}


def test_batch_list_serializes_as_array():
    logs = [Log(message="a"), Log(message="b")]
    payload = json.loads(serialize_body(logs))
    assert isinstance(payload, list)
    assert [x["message"] for x in payload] == ["a", "b"]


def test_batch_strips_none_inside_list_items():
    # Regression: batch payloads must strip None fields the same way
    # single-dataclass payloads do, so the server never sees
    # "timestamp":null (which it rejects as invalid DateTime).
    logs = [Log(message="a"), Log(message="b")]
    payload = json.loads(serialize_body(logs))
    for item in payload:
        assert "timestamp" not in item
        assert "application" not in item
        assert None not in item.values()


def test_level_serializes_as_wire_name_not_int():
    # Regression: the server's REST LogLevel enum is
    # Info=0, Warning=1, Error=2, Critical=3, Exception=4, Debug=5 —
    # NOT the SDK's int values. Sending the int would store every log
    # under the wrong level. Wire format must be the string name.
    from logdbhq.models import LogLevel

    cases = {
        LogLevel.Debug: "Debug",
        LogLevel.Info: "Info",
        LogLevel.Warning: "Warning",
        LogLevel.Error: "Error",
        LogLevel.Critical: "Critical",
        LogLevel.Exception: "Exception",
    }
    for level, wire in cases.items():
        body = json.loads(serialize_body(Log(message="t", level=level)))
        assert body["level"] == wire, f"{level} should serialize as {wire!r}"

    # Trace has no server-side equivalent; SDK maps it to Debug.
    body = json.loads(serialize_body(Log(message="t", level=LogLevel.Trace)))
    assert body["level"] == "Debug"


def test_level_in_batch_also_serializes_as_wire_name():
    from logdbhq.models import LogLevel

    batch = [Log(message="a", level=LogLevel.Info), Log(message="b", level=LogLevel.Error)]
    payload = json.loads(serialize_body(batch))
    assert payload[0]["level"] == "Info"
    assert payload[1]["level"] == "Error"
