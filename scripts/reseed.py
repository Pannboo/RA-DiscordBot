"""Factory-reset event history and re-seed silently from RA's current state.

After running this, the bot's "seen" tables contain everything the RA API
currently shows for each linked user. The next poll will therefore only emit
events for things that change *after* this script ran — no replays of historical
unlocks, awards, sessions, or milestones.

Usage:
    .venv/bin/python -m scripts.reseed
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select

from bot.db import get_session, init_engine, shutdown_engine
from bot.db.models import AwardSeen, GameSession, MilestoneSeen, UnlockSeen, User
from bot.poller.detector import _classify_award
from bot.ra import RAClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("reseed")


async def _seed_user(ra: RAClient, user_id: int, ra_username: str) -> None:
    log.info("reseeding %s (%s)…", ra_username, user_id)

    # 1. Recent unlocks (last 60min) — silently insert into unlocks_seen.
    recent = await ra.get_user_recent_achievements(ra_username, minutes=60)
    unlock_count = 0
    last_unlock_at: datetime | None = None
    async with get_session() as db:
        for a in recent:
            db.add(
                UnlockSeen(
                    ra_username=ra_username,
                    achievement_id=a.achievement_id,
                    hardcore=a.is_hardcore,
                    unlocked_at=a.date,
                )
            )
            unlock_count += 1
            if last_unlock_at is None or a.date > last_unlock_at:
                last_unlock_at = a.date
        await db.commit()
    log.info("  unlocks seeded: %d", unlock_count)

    # 2. Awards — silently insert all into awards_seen.
    awards = await ra.get_user_awards(ra_username)
    award_count = 0
    if awards is not None:
        async with get_session() as db:
            for entry in awards.visible_user_awards:
                kind = _classify_award(entry)
                if kind is None or entry.award_data is None:
                    continue
                db.add(
                    AwardSeen(
                        ra_username=ra_username,
                        game_id=entry.award_data,
                        kind=kind,
                        awarded_at=entry.awarded_at,
                    )
                )
                award_count += 1
            await db.commit()
    log.info("  awards seeded: %d", award_count)

    # 3. Current session — seed game_sessions from summary.LastGame.
    summary = await ra.get_user_summary(ra_username, recent_games=1)
    if summary is None:
        log.warning("  no summary returned — leaving session/totals untouched")
        return

    if summary.last_game:
        gid = summary.last_game.get("ID")
        if gid is not None:
            async with get_session() as db:
                db.add(
                    GameSession(
                        ra_username=ra_username,
                        game_id=int(gid),
                        started_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
            log.info(
                "  session seeded: game %s (%s)", gid, summary.last_game.get("Title", "?")
            )

    # 4. User row — cache the current totals so milestones don't all fire on
    #    the next poll, and reset last_unlock_at + polled timestamps so the
    #    next tick treats this user as fresh-but-known.
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is not None:
            user.total_points = summary.total_points
            user.total_softcore_points = summary.total_softcore_points
            user.total_retro_points = summary.total_true_points
            user.last_unlock_at = last_unlock_at
            user.last_presence_active_at = None
            user.last_polled_at = datetime.now(timezone.utc)
            user.last_awards_polled_at = datetime.now(timezone.utc)
            # Schedule next poll on the normal idle cadence — caller can override.
            user.next_poll_at = datetime.now(timezone.utc)
            await db.commit()
    log.info(
        "  totals cached: %d HC / %d SC / %d RP",
        summary.total_points,
        summary.total_softcore_points,
        summary.total_true_points,
    )


async def amain() -> None:
    init_engine()
    ra = RAClient()
    try:
        # 1. Wipe all event-tracking tables.
        async with get_session() as db:
            await db.execute(delete(UnlockSeen))
            await db.execute(delete(AwardSeen))
            await db.execute(delete(GameSession))
            await db.execute(delete(MilestoneSeen))
            await db.commit()
        log.info("wiped unlocks_seen / awards_seen / game_sessions / milestones_seen")

        # 2. For every linked user, re-seed silently from the RA API.
        async with get_session() as db:
            users = (
                await db.execute(select(User.discord_id, User.ra_username))
            ).all()
        if not users:
            log.warning("no linked users — nothing to seed")
            return

        for discord_id, ra_username in users:
            try:
                await _seed_user(ra, discord_id, ra_username)
            except Exception:
                log.exception("seed failed for %s", ra_username)

        log.info("reseed complete — next poll will only emit NEW events")
    finally:
        await ra.aclose()
        await shutdown_engine()


if __name__ == "__main__":
    asyncio.run(amain())
