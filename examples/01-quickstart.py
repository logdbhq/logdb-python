"""Quickstart: minimal writer example.

Installs -> one log -> flush -> close. Runs anywhere with a LOGDB_API_KEY
env var set.

    pip install logdbhq
    LOGDB_API_KEY=sk-... python examples/01-quickstart.py
"""

from __future__ import annotations

import os

from logdbhq import Log, LogDBClient, LogLevel


def main() -> None:
    api_key = os.environ["LOGDB_API_KEY"]

    # Context manager guarantees flush + close, even on exceptions.
    with LogDBClient(
        api_key=api_key,
        default_application="quickstart-example",
        default_environment=os.environ.get("ENV", "development"),
    ) as client:
        status = client.log(
            Log(
                message="hello from python",
                level=LogLevel.Info,
                attributesS={"source": "quickstart"},
                attributesN={"value": 42},
            )
        )
        print("log status:", status.value)


if __name__ == "__main__":
    main()
