"""Wire `logdbhq` into stdlib logging.

A real-world adoption pattern: you already use `logging` across your
Django / Flask / FastAPI / CLI app, and you want every log to also land
in LogDB without rewriting every call site.
"""

from __future__ import annotations

import logging
import os

from logdbhq import LogDBClient
from logdbhq.logging_handler import LogDBHandler


def main() -> None:
    # One client per process — singleton-style.
    client = LogDBClient(
        api_key=os.environ["LOGDB_API_KEY"],
        default_application="logging-example",
    )

    # Plug into the root logger so every getLogger(...) in your app inherits.
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(LogDBHandler(client))

    # Use `logging` exactly as you always have.
    app_log = logging.getLogger("my-app")
    app_log.info("user signed in", extra={"user_id": 42, "from": "web"})
    app_log.warning("slow query", extra={"duration_ms": 842.3, "table": "orders"})

    try:
        raise ValueError("bad input")
    except ValueError:
        app_log.exception("request failed")

    # Flush + close on shutdown.
    client.close()


if __name__ == "__main__":
    main()
