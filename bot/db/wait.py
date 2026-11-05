"""Find the database and block until it accepts connections.

Run at container start, before migrations:

    DATABASE_URL="$(python -m bot.db.wait)"

On success the working database URL is printed to stdout (logs go to stderr),
so the caller can export it for alembic and the bot.

For Postgres, if the configured host doesn't answer, it searches:

1. the configured host (DATABASE_URL, or DB_HOST, default "db")
2. common hostnames for a database service ("db", "postgres", "database", ...)
3. every address on this container's own /24 network that has the Postgres
   port open, using the configured username, password and database name

So the bot finds Postgres on the same Docker network whatever the service or
container is called. Set DB_DISCOVERY=false to only use the configured host.

This also covers the case where Compose's `depends_on: service_healthy` isn't
honoured (the Docker daemon restarting containers after a host reboot): without
waiting, `alembic upgrade head` fails immediately and the container
crash-loops.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import sys
import time

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from bot.config import get_settings

log = logging.getLogger("ra_bot.wait_for_db")

FALLBACK_HOSTS = [
    "db",
    "postgres",
    "postgresql",
    "database",
    "pg",
    "host.docker.internal",
    "localhost",
]
CONNECT_TIMEOUT = 5.0
PORT_PROBE_TIMEOUT = 0.5
PORT_PROBE_CONCURRENCY = 64


def _safe(url: URL) -> str:
    return url.render_as_string(hide_password=True)


async def _try_connect(url: URL) -> Exception | None:
    """Return None if `url` accepts a query, else the error."""
    connect_args = {"timeout": CONNECT_TIMEOUT} if url.drivername.endswith("asyncpg") else {}
    engine = create_async_engine(url, pool_pre_ping=False, connect_args=connect_args)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return None
    except Exception as exc:  # noqa: BLE001 - any failure means "not this one / not yet"
        return exc
    finally:
        await engine.dispose()


async def _resolves(host: str) -> bool:
    try:
        await asyncio.get_running_loop().getaddrinfo(host, None)
        return True
    except OSError:
        return False


def _own_ipv4() -> ipaddress.IPv4Address | None:
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(socket.gethostname()))
    except OSError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address) or addr.is_loopback:
        return None
    return addr


async def _port_open(host: str, port: int, sem: asyncio.Semaphore) -> bool:
    async with sem:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), PORT_PROBE_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True


async def _stable_name(ip: str) -> str:
    """Prefer a hostname over a scanned IP.

    Docker's DNS answers reverse lookups for containers on the same network,
    so this turns e.g. 172.18.0.2 into "RetroCheevs-Database.mystack_default".
    The bot then keeps finding the database by name after it is recreated with
    a new IP (a redeploy), instead of holding on to a stale address.
    """
    loop = asyncio.get_running_loop()
    try:
        name, _, _ = await loop.run_in_executor(None, socket.gethostbyaddr, ip)
        infos = await loop.getaddrinfo(name, None)
    except OSError:
        return ip
    if any(info[4][0] == ip for info in infos):
        return name
    return ip


async def _scan_local_network(port: int) -> list[str]:
    """Addresses on this container's /24 with `port` open."""
    me = _own_ipv4()
    if me is None:
        return []
    network = ipaddress.ip_network(f"{me}/24", strict=False)
    hosts = [str(h) for h in network.hosts() if h != me]
    sem = asyncio.Semaphore(PORT_PROBE_CONCURRENCY)
    results = await asyncio.gather(*(_port_open(h, port, sem) for h in hosts))
    found = [h for h, ok in zip(hosts, results, strict=True) if ok]
    log.info("Scanned %s for port %d: %s", network, port, ", ".join(found) or "nothing found")
    return found


async def _find_once(base: URL, discovery: bool) -> URL | None:
    """One pass over every candidate. Returns the first URL that works."""
    err = await _try_connect(base)
    if err is None:
        return base
    log.warning("Database at %s not reachable: %s", _safe(base), err)

    if not discovery or not base.drivername.startswith("postgresql"):
        return None

    port = base.port or 5432
    tried = {base.host}
    for host in FALLBACK_HOSTS:
        if host in tried or not await _resolves(host):
            continue
        tried.add(host)
        candidate = base.set(host=host, port=port)
        if await _try_connect(candidate) is None:
            return candidate

    for host in await _scan_local_network(port):
        if host in tried:
            continue
        tried.add(host)
        candidate = base.set(host=host, port=port)
        err = await _try_connect(candidate)
        if err is None:
            name = await _stable_name(host)
            if name != host:
                named = base.set(host=name, port=port)
                if await _try_connect(named) is None:
                    return named
            return candidate
        log.info("Port %d open on %s but login failed: %s", port, host, err)
    return None


async def find_database(timeout: float, interval: float) -> URL | None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        missing = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        log.error(
            "Bot settings are invalid or missing: %s. Set them in the bot's "
            "`environment:` (or `env_file:`) in docker-compose.yml.",
            ", ".join(missing),
        )
        return None
    base = make_url(settings.database_url)
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        found = await _find_once(base, settings.db_discovery)
        if found is not None:
            if found.host != base.host:
                log.info("Found the database at %s instead of %s", found.host, base.host)
            log.info("Database is ready at %s (attempt %d)", _safe(found), attempt)
            return found
        if time.monotonic() >= deadline:
            log.error("No database found after %.0fs", timeout)
            return None
        log.warning("Database not ready (attempt %d), retrying in %.0fs", attempt, interval)
        await asyncio.sleep(interval)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    timeout = float(os.environ.get("DB_WAIT_TIMEOUT", "300"))
    interval = float(os.environ.get("DB_WAIT_INTERVAL", "2"))
    found = asyncio.run(find_database(timeout, interval))
    if found is None:
        sys.exit(1)
    print(found.render_as_string(hide_password=False))


if __name__ == "__main__":
    main()
