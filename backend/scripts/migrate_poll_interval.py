import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DB_URI = os.environ.get(
    "SERVER_MONITOR_DATABASE_URI",
    "mysql+asyncmy://root:changeme@db:3306/server_monitor",
)

STATEMENT = """
ALTER TABLE monitored_backends
    ADD COLUMN poll_interval_seconds INT NOT NULL DEFAULT 60
"""


async def migrate() -> None:
    engine = create_async_engine(DB_URI, isolation_level="AUTOCOMMIT")
    async with engine.begin() as conn:
        await conn.execute(text(STATEMENT))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
