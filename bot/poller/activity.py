from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from sqlalchemy import select

from bot.db import get_session
from bot.db.models import User

log = logging.getLogger(__name__)


class PresenceTracker:
    """Updates `users.last_presence_active_at` whenever a linked Discord user
    is detected playing a game (Activity of type `playing`).

    Requires the Presences privileged intent.
    """

    def __init__(self, bot: discord.Client) -> None:
        self._bot = bot

    def install(self) -> None:
        self._bot.add_listener(self.on_presence_update, "on_presence_update")

    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        # Find a "playing" activity if any.
        playing = any(
            isinstance(a, discord.Game)
            or (isinstance(a, discord.Activity) and a.type == discord.ActivityType.playing)
            for a in (after.activities or [])
        )
        if not playing:
            return

        async with get_session() as session:
            user = await session.scalar(select(User).where(User.discord_id == after.id))
            if user is None:
                return
            now = datetime.now(timezone.utc)
            user.last_presence_active_at = now
            # Bump up to the active tier immediately so the next tick polls fast.
            if user.next_poll_at is None or user.next_poll_at > now:
                user.next_poll_at = now
            await session.commit()
