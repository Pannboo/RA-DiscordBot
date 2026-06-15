"""Per-console game-list cache used by name-based `/ra game` lookups.

RA's game catalogue per console is large but stable. We fetch on demand and
keep the result in memory for the bot's lifetime — refreshes happen
implicitly on process restart, which is enough for a name index. Lookups are
single-flight per console so concurrent commands don't trigger duplicate
fetches.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bot.ra.client import RAClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedGame:
    id: int
    title: str
    console_id: int
    console_name: str


_cache: dict[int, list[CachedGame]] = {}
_locks: dict[int, asyncio.Lock] = {}


def _lock_for(console_id: int) -> asyncio.Lock:
    lock = _locks.get(console_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[console_id] = lock
    return lock


async def get_games(ra: RAClient, console_id: int) -> list[CachedGame]:
    """Return the cached game list for a console, fetching it the first time."""
    cached = _cache.get(console_id)
    if cached is not None:
        return cached

    async with _lock_for(console_id):
        cached = _cache.get(console_id)
        if cached is not None:
            return cached

        raw = await ra.get_game_list(console_id)
        games = [
            CachedGame(
                id=int(g["ID"]),
                title=str(g.get("Title") or "").strip(),
                console_id=int(g.get("ConsoleID") or console_id),
                console_name=str(g.get("ConsoleName") or "").strip(),
            )
            for g in raw
            if "ID" in g
        ]
        _cache[console_id] = games
        log.info("cached %d games for console_id=%s", len(games), console_id)
        return games


def search(games: list[CachedGame], query: str, limit: int = 25) -> list[CachedGame]:
    """Rank games by how well they match `query`.

    Strict exact match wins, then prefix, then substring. Stable within each
    bucket by alphabetical title so results don't shuffle between calls.
    """
    q = query.strip().lower()
    if not q:
        return []

    exact: list[CachedGame] = []
    prefix: list[CachedGame] = []
    substring: list[CachedGame] = []
    for g in games:
        t = g.title.lower()
        if t == q:
            exact.append(g)
        elif t.startswith(q):
            prefix.append(g)
        elif q in t:
            substring.append(g)

    exact.sort(key=lambda g: g.title.lower())
    prefix.sort(key=lambda g: g.title.lower())
    substring.sort(key=lambda g: g.title.lower())
    return (exact + prefix + substring)[:limit]
