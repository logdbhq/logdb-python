"""Fluent builders — for callers coming from the .NET / Node / PHP SDKs."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from logdbhq import (
    LogBeatBuilder,
    LogCacheBuilder,
    LogDBClient,
    LogEventBuilder,
    LogLevel,
)


def main() -> None:
    trace_id = str(uuid.uuid4())

    with LogDBClient(
        api_key=os.environ["LOGDB_API_KEY"],
        default_application="builders-example",
    ) as client:
        # Log event.
        status = (
            LogEventBuilder.create(client)
            .set_message("payment processed")
            .set_log_level(LogLevel.Info)
            .set_correlation_id(trace_id)
            .set_user_email("alice@example.com")
            .set_request_path("/api/checkout")
            .set_http_method("POST")
            .set_status_code(200)
            .add_attribute("currency", "EUR")
            .add_attribute("amount_eur", 199.99)
            .add_attribute("fraud_reviewed", True)
            .add_attribute("completed_at", datetime.now(tz=timezone.utc))
            .add_label("payment")
            .add_label("checkout")
            .log()
        )
        print("log status:", status.value)

        # Heartbeat / metric.
        (
            LogBeatBuilder.create(client)
            .set_measurement("worker_health")
            .add_tag("worker_id", "worker-1")
            .add_tag("region", "eu-west-1")
            .add_field("cpu_percent", 23.4)
            .add_field("healthy", True)
            .log()
        )

        # Key/value cache row — arbitrary JSON.
        (
            LogCacheBuilder.create(client)
            .set_key(f"user:42:session:{trace_id[:8]}")
            .set_value({"ip": "203.0.113.10", "ua": "Mozilla/5.0"})
            .log()
        )


if __name__ == "__main__":
    main()
