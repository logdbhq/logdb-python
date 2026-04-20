"""Async flavor of the writer + reader.

Use from FastAPI / aiohttp / trio (via anyio) handlers where you can't
block on network I/O.
"""

from __future__ import annotations

import asyncio
import os

from logdbhq import AsyncLogDBClient, AsyncLogDBReader, Log, LogLevel


async def main() -> None:
    api_key = os.environ["LOGDB_API_KEY"]

    async with AsyncLogDBClient(
        api_key=api_key,
        default_application="async-example",
    ) as client:
        # Fan out a handful of logs concurrently.
        await asyncio.gather(
            client.log(Log(message=f"event {i}", level=LogLevel.Info))
            for i in range(5)
        )
        await client.flush()

    async with AsyncLogDBReader(api_key=api_key) as reader:
        page = await reader.get_logs()
        print(f"last query: {page.totalCount} rows")


if __name__ == "__main__":
    asyncio.run(main())
