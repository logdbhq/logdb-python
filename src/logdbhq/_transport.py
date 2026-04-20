"""Thin HTTP transport layer over :mod:`httpx` shared by client and reader.

Concrete responsibilities:

* Build request headers (``X-LogDB-ApiKey``, Content-Type, user headers).
* Normalize HTTP failures into the SDK's error hierarchy
  (:class:`~logdbhq.LogDBAuthError`, :class:`~logdbhq.LogDBConfigError`,
  :class:`~logdbhq.LogDBNetworkError`, :class:`~logdbhq.LogDBTimeoutError`).
* Serialize outbound models with ``datetime`` → ISO-8601 Z normalization,
  stripping ``None`` values so the server sees a minimal payload.
* Deserialize responses and parse ISO-8601 strings back to :class:`datetime`.

Kept small on purpose — retry, circuit breaking, and batching live in
``resilience.py`` and ``batching.py`` so each concern is independently
testable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

import httpx

from .errors import (
    LogDBAuthError,
    LogDBConfigError,
    LogDBError,
    LogDBNetworkError,
    LogDBTimeoutError,
)


JSON_CONTENT_TYPE = "application/json"


def _json_default(obj: Any) -> Any:
    """datetime → ISO-8601 with explicit UTC ``Z``; dataclasses → dict."""
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _strip_none(value: Any) -> Any:
    """Recursively drop ``None`` values so the server sees a minimal
    payload. Cheap enough to do on every request."""
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def serialize_body(payload: Any) -> str:
    """Turn dataclasses / dicts / lists into a JSON body string."""
    if is_dataclass(payload) and not isinstance(payload, type):
        payload = asdict(payload)
    payload = _strip_none(payload)
    return json.dumps(payload, default=_json_default, separators=(",", ":"))


def build_headers(
    *,
    api_key: Optional[str],
    extra: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": JSON_CONTENT_TYPE}
    if api_key:
        headers["X-LogDB-ApiKey"] = api_key
    if extra:
        headers.update(extra)
    return headers


# ──────────────────────────────────────────────────────────────────────
# Response classification
# ──────────────────────────────────────────────────────────────────────


def _classify(status: int, body: str, url: str) -> LogDBError:
    """Map an HTTP status + body to the right SDK exception."""
    message = f"HTTP {status} from {url}"
    if body:
        snippet = body if len(body) <= 400 else body[:400] + "…"
        message = f"{message}: {snippet}"

    if status in (401, 403):
        return LogDBAuthError(message)
    if status in (400, 404, 422):
        return LogDBConfigError(message)
    return LogDBNetworkError(message)


def raise_for_status(response: httpx.Response) -> None:
    """Raise the SDK-shaped error for a non-2xx response. 204 and 200 pass."""
    if response.status_code < 400:
        return
    body = ""
    try:
        body = response.text
    except Exception:
        pass
    raise _classify(response.status_code, body, str(response.url))


def translate_request_error(exc: httpx.HTTPError, url: str) -> LogDBError:
    """Convert an httpx transport error into an SDK exception."""
    if isinstance(exc, httpx.TimeoutException):
        return LogDBTimeoutError(f"Request to {url} timed out", cause=exc)
    return LogDBNetworkError(f"Request to {url} failed: {exc}", cause=exc)
