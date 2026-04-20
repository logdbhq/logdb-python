# logdbhq

[![PyPI](https://img.shields.io/pypi/v/logdbhq.svg)](https://pypi.org/project/logdbhq/)
[![Python](https://img.shields.io/pypi/pyversions/logdbhq.svg)](https://pypi.org/project/logdbhq/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

LogDB SDK for Python — sync + async writer, reader, stdlib `logging` handler, batching, retries, circuit breaker. Zero-config service discovery, one dependency (`httpx`).

```bash
pip install logdbhq
```

```python
from logdbhq import LogDBClient, Log, LogLevel

with LogDBClient(api_key="sk-…", default_application="my-app") as client:
    client.log(Log(message="hello", level=LogLevel.Info))
```

That's it. The client discovers the LogDB REST endpoint, batches in the background, retries transient failures, and flushes on context-manager exit. No servers, relays, or extra config.

## Why

- **Sync *and* async, one library.** Both clients are first-class. Use `LogDBClient` from Django/Flask/workers; `AsyncLogDBClient` from FastAPI/aiohttp.
- **Writer + Reader from v0.1.** Log events, beats, and cache writes; query everything back. No "reader coming soon."
- **Drop-in stdlib `logging` adapter.** `logging.getLogger().addHandler(LogDBHandler(client))` — every `log.info("…")` in your app goes to LogDB with zero rewrites.
- **Fluent builders** for progressive enrichment and parity with the .NET / Node / PHP SDKs.
- **Production hygiene built in.** Exponential-backoff retries with jitter, sliding-window circuit breaker, batching (per-type buffer with size + time triggers), structured error classes.
- **Type-hinted throughout.** `py.typed` (MIT license, strict mypy-clean).

## Install

```bash
pip install logdbhq
```

Requirements: Python 3.9+. One runtime dep: [`httpx`](https://www.python-httpx.org/).

## Quickstart

```python
import os
from logdbhq import LogDBClient, Log, LogLevel

# Context manager → guaranteed flush + close on exit.
with LogDBClient(
    api_key=os.environ["LOGDB_API_KEY"],
    default_application="my-app",
    default_environment=os.environ.get("ENV", "production"),
) as client:
    client.log(Log(
        message="checkout completed",
        level=LogLevel.Info,
        correlationId=trace_id,
        userEmail="alice@example.com",
        attributesN={"amount_eur": 199.99},
        attributesS={"currency": "EUR"},
        label=["payment"],
    ))
```

The `LOGDB_API_KEY` environment variable is picked up automatically by `LogDBClientOptions.from_env()` if you prefer that over explicit passing. The REST endpoint is discovered at runtime from `https://discovery.logdb.site/get/rest-api` — override with `endpoint=` or `LOGDB_REST_URL`.

## stdlib `logging` adapter

The fastest way to adopt LogDB in an existing Python app: one line.

```python
import logging
from logdbhq import LogDBClient
from logdbhq.logging_handler import LogDBHandler

client = LogDBClient(api_key="sk-…", default_application="my-app")
logging.getLogger().addHandler(LogDBHandler(client))

logging.info("user signed in", extra={"user_id": 42, "healthy": True})
```

The handler maps stdlib levels to `LogLevel`, routes your `extra` kwargs into typed attribute maps by Python type (`int/float` → `attributesN`, `bool` → `attributesB`, `datetime` → `attributesD`, everything else → `attributesS`), and attaches `exc_info` to the log's exception/stackTrace.

## Async

Same API, awaitable. Built on `httpx.AsyncClient` so it cooperates with `asyncio` / `anyio` / FastAPI.

```python
import asyncio
from logdbhq import AsyncLogDBClient, Log, LogLevel

async def main():
    async with AsyncLogDBClient(api_key="sk-…") as client:
        await client.log(Log(message="starting", level=LogLevel.Info))
        await client.flush()

asyncio.run(main())
```

## Reader / query

```python
from datetime import datetime, timedelta, timezone
from logdbhq import LogDBReader, LogQueryParams

with LogDBReader(api_key="sk-…") as reader:
    page = reader.get_logs(LogQueryParams(
        application="my-app",
        level="Error",
        fromDate=datetime.now(tz=timezone.utc) - timedelta(days=1),
        take=50,
    ))
    for entry in page.items:
        print(entry.timestamp, entry.message)

    # Count-only, skips row materialization
    total = reader.get_logs_count(LogQueryParams(level="Error"))

    # Metadata
    collections = reader.get_collections()
    status = reader.get_event_log_status()
```

Reader methods **throw** on failure (unlike writers, which return a `LogResponseStatus`). Catch `LogDBError` for a blanket handler, or one of `LogDBAuthError` / `LogDBNetworkError` / `LogDBTimeoutError` / `LogDBConfigError` for targeted recovery.

## Fluent builders

For progressively-enriched patterns, or parity with the other SDKs:

```python
from logdbhq import LogEventBuilder, LogLevel

(LogEventBuilder.create(client)
    .set_message("payment processed")
    .set_log_level(LogLevel.Info)
    .set_correlation_id(trace_id)
    .set_user_email("alice@example.com")
    .add_attribute("amount_eur", 199.99)
    .add_attribute("fraud_reviewed", True)
    .add_label("payment")
    .log())
```

Also: `LogBeatBuilder` (for metrics/heartbeats) and `LogCacheBuilder` (for key/value writes). Builders are immutable — each setter returns a new instance — so a base builder can be shared across handlers.

## Configuration

Most callers only set `api_key` and `default_application`. Every option has a reasonable default.

| Option | Default | Description |
|---|---|---|
| `api_key` | from `LOGDB_API_KEY` | Server API key. Required. |
| `endpoint` | *discovered* | Full REST base URL. Overrides discovery. |
| `discovery_url` | `discovery.logdb.site/get/rest-api` | Service-discovery endpoint. |
| `default_application` | — | Stamped as `application` on every log. |
| `default_environment` | `"production"` | Stamped as `environment`. |
| `default_collection` | `"logs"` | Stamped as `collection`. |
| `enable_batching` | `True` | Buffer + flush by size/time. |
| `batch_size` | `100` | Entries per type before flush. |
| `flush_interval` | `5.0` s | Oldest entry's max wait. |
| `max_retries` | `3` | Per-call retry attempts on transient failures. |
| `retry_delay` | `1.0` s | Initial backoff. |
| `retry_backoff_multiplier` | `2.0` | Exponential multiplier (±20% jitter). |
| `enable_circuit_breaker` | `True` | Sliding-window failure-rate breaker. |
| `request_timeout` | `30.0` s | Per-request deadline. |
| `headers` | `{}` | Extra HTTP headers on every request. |
| `on_error` | `None` | Callback: `(exc, batch) -> None`. |

Matches `@logdbhq/node`'s surface one-to-one (with `snake_case` instead of `camelCase`).

## Error model

Writers are **non-throwing** on transient failures — every send returns a `LogResponseStatus`:

```python
from logdbhq import LogResponseStatus

status = client.log(Log(message="…"))
if status == LogResponseStatus.NotAuthorized:
    # API key is wrong / revoked
elif status == LogResponseStatus.CircuitOpen:
    # Too many recent failures — breaker is protecting upstream
elif status == LogResponseStatus.Timeout:
    # Request deadline exceeded
elif status != LogResponseStatus.Success:
    # Something else (5xx after retries, network failure)
```

Subscribe via `on_error` to see the raw exception + batch:

```python
def on_error(exc, batch):
    app_log.warning("LogDB delivery failed: %s (%d items)", exc, len(batch) if batch else 0)

LogDBClient(api_key="sk-…", on_error=on_error)
```

Reader methods raise instead — classification is the same (`LogDBAuthError`, `LogDBNetworkError`, `LogDBTimeoutError`, `LogDBConfigError`, `LogDBCircuitOpenError`, all descending from `LogDBError`).

## Batching

Enabled by default. Separate buffer per entry type so a flood of one type never delays the others. Tunable:

```python
LogDBClient(
    api_key="sk-…",
    enable_batching=True,
    batch_size=50,          # flush when any buffer reaches this size
    flush_interval=2.0,     # seconds an entry waits before forced flush
    max_batch_retries=2,
)
```

For must-not-lose scenarios (serverless handlers, cron jobs), disable:

```python
LogDBClient(api_key="sk-…", enable_batching=False, max_retries=5)
```

## Circuit breaker

Sliding-window failure-rate. Configurable threshold + windows. When the rate in the sampling window crosses the threshold, the breaker opens and `LogDBCircuitOpenError` raises synchronously (no wasted network hits). After `circuit_breaker_duration_of_break` elapses, it transitions to half-open and probes with the next real request.

```python
LogDBClient(
    api_key="sk-…",
    enable_circuit_breaker=True,
    circuit_breaker_failure_threshold=0.5,   # 50%
    circuit_breaker_sampling_duration=10.0,  # 10s window
    circuit_breaker_duration_of_break=30.0,  # 30s open
)
```

## When to use `@logdbhq/web` vs `logdbhq` (Python)

| | `logdbhq` (Python) | `@logdbhq/web` (browser) |
|---|---|---|
| Runtime | Server / script / worker / notebook | Browser only |
| Auth | Server API key (`logdb_sk_…`) | Publishable key (`logdb_pk_…`), origin-locked |
| Transport | JSON over HTTPS directly to LogDB | JSON to LogDB hosted relay |

Use Python for server-side code and the browser SDK for client bundles. Both speak the same wire format, so events from either round-trip cleanly through LogDB's reader.

## Examples

Runnable scripts under [`examples/`](./examples/):

- `01-quickstart.py` — basic writer
- `02-logging-handler.py` — stdlib `logging` integration
- `03-reader.py` — query API
- `04-async.py` — asyncio flavor
- `05-builders.py` — fluent builders for log/beat/cache

## Development

```bash
git clone https://github.com/logdbhq/logdb-python
cd logdb-python
pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

## License

MIT — see [LICENSE](./LICENSE).

## See also

- Documentation: [docs.logdb.dev](https://docs.logdb.dev)
- Source: [github.com/logdbhq/logdb-python](https://github.com/logdbhq/logdb-python)
- Sibling SDKs: [`@logdbhq/node`](https://github.com/logdbhq/logdb-node) · [`@logdbhq/web`](https://github.com/logdbhq/logdb-web) · [`logdbhq/logdb-php`](https://github.com/logdbhq/logdb-php)
