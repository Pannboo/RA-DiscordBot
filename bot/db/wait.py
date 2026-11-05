"""Block until the database accepts connections.

Run before migrations at container start (`python -m bot.db.wait`). Compose's
`depends_on: service_healthy` is only honoured by `docker compose up`; when the
Docker daemon itself restarts containers (host reboot, daemon restart) the bot
can start before Postgres has finished crash recovery. Without this wait,
`alembic upgrade head` fails immediately and the container crash-loops.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bot.config import get_settings

log = logging.getLogger("ra_bot.wait_for_db")


async def wait_for_db(timeout: float, interval: float) -> bool:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=False)
    deadline = time.monotonic() + timeout
    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                log.info("Database is ready (attempt %d)", attempt)
                return True
            except Exception as exc:  # noqa: BLE001 - any failure means "not ready yet"
                if time.monotonic() >= deadline:
                    log.error("Database still unavailable after %.0fs: %s", timeout, exc)
                    return False
                log.warning("Database not ready (attempt %d): %s", attempt, exc)
                await asyncio.sleep(interval)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    timeout = float(os.environ.get("DB_WAIT_TIMEOUT", "300"))
    interval = float(os.environ.get("DB_WAIT_INTERVAL", "2"))
    ok = asyncio.run(wait_for_db(timeout, interval))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
