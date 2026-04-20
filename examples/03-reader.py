"""Query logs back out of LogDB."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from logdbhq import LogDBReader, LogQueryParams


def main() -> None:
    with LogDBReader(api_key=os.environ["LOGDB_API_KEY"]) as reader:
        # Errors in the last 24 hours for a given application.
        page = reader.get_logs(
            LogQueryParams(
                application="my-app",
                level="Error",
                fromDate=datetime.now(tz=timezone.utc) - timedelta(days=1),
                take=50,
            )
        )
        print(f"{page.totalCount} total errors, showing {len(page.items)}")
        for entry in page.items[:5]:
            print(f"  {entry.timestamp}  {entry.message}")

        # List known collections in the tenant.
        collections = reader.get_collections()
        print("collections:", collections)

        # Just the count — skips materializing rows.
        count = reader.get_logs_count(LogQueryParams(level="Error"))
        print(f"total Error rows: {count}")


if __name__ == "__main__":
    main()
