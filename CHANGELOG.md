# Changelog

All notable changes to `logdbhq` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0-alpha.0] - 2026-04-20

Initial public release.

### Added

- **Writer (sync + async)** — `LogDBClient` and `AsyncLogDBClient` with
  `log` / `log_beat` / `log_cache` + their batch variants. Both are context
  managers (sync `with` / async `async with`) with guaranteed flush on exit.
- **Reader (sync + async)** — `LogDBReader` / `AsyncLogDBReader` with
  `get_logs`, `get_log_caches`, `get_log_beats`, `get_logs_count`,
  `get_collections`, `get_event_log_status`. Mirrors the PHP and Node SDK
  surfaces.
- **Fluent builders** — `LogEventBuilder`, `LogBeatBuilder`, `LogCacheBuilder`.
  Immutable (each setter returns a new instance), so sharing a base builder
  across handlers is safe.
- **stdlib `logging` handler** — `logdbhq.logging_handler.LogDBHandler`
  plugs the SDK into Python's built-in logging. Maps levels, routes
  `extra` kwargs into typed attribute maps (string/number/bool/date), and
  captures `exc_info` on exception logs.
- **Batching** — per-type buffer with size and time triggers
  (`batch_size=100`, `flush_interval=5.0`s default). Separate
  implementations for sync (`threading.Thread`) and async
  (`asyncio.Task`), no shared locks between the two.
- **Retry policy** — exponential backoff with ±20% jitter. Only transient
  errors (network, 5xx, timeouts) retry; `LogDBAuthError` and
  `LogDBConfigError` fail fast.
- **Circuit breaker** — sliding-window failure-rate breaker, opens when
  the rate crosses `circuit_breaker_failure_threshold` (default 50%)
  within `circuit_breaker_sampling_duration` (default 10s), stays open
  for `circuit_breaker_duration_of_break` (default 30s), then probes via
  a half-open state.
- **Service discovery** — REST endpoint resolved at runtime from
  `https://discovery.logdb.site/get/rest-api`. Override with
  `endpoint=` / `LOGDB_REST_URL`. One-shot cache per client lifetime;
  failed discoveries are not cached.
- **Typed error hierarchy** — `LogDBError` base with
  `LogDBAuthError`, `LogDBConfigError`, `LogDBNetworkError`,
  `LogDBTimeoutError`, `LogDBCircuitOpenError` subclasses.
- **Env var support** — `LogDBClientOptions.from_env()` /
  `LogDBReaderOptions.from_env()` read `LOGDB_API_KEY`,
  `LOGDB_REST_URL`, `LOGDB_DISCOVERY_URL`, `LOGDB_DEFAULT_APPLICATION`,
  `LOGDB_DEFAULT_ENVIRONMENT`.

### Dependencies

- `httpx>=0.26,<1.0` — single runtime dep. Ships both sync and async
  HTTP clients out of the box.

### Out of scope for v0.1

- OpenTelemetry exporter (roadmap).
- Encryption (roadmap; tracking parity with the other SDKs' planned
  AES-GCM support).
- gRPC transport. The Python SDK uses REST exclusively. `@logdbhq/node`
  ships a gRPC-Web transport; Python will likely follow if real
  performance data argues for it.
