from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from sqlalchemy import select

from bot.db import get_session
from bot.db.models import GuildConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _GuildEntry:
    channel_id: int
    # 0 disables the "user resumed a game after a gap" re-announce for this guild.
    replay_announce_after_minutes: int


class Dispatcher:
    """Fans an event out to every guild the linked Discord user is a member of,
    posting to that guild's configured announce channel.
    """

    def __init__(self, bot: discord.Client) -> None:
        self._bot = bot
        # In-memory cache: guild_id -> entry (or None if disabled).
        self._cache: dict[int, _GuildEntry | None] = {}

    async def refresh(self) -> None:
        async with get_session() as session:
            rows = (await session.execute(select(GuildConfig))).scalars().all()
        cache: dict[int, _GuildEntry | None] = {}
        for cfg in rows:
            if not cfg.enabled or cfg.announce_channel_id is None:
                cache[cfg.guild_id] = None
                continue
            cache[cfg.guild_id] = _GuildEntry(
                channel_id=cfg.announce_channel_id,
                replay_announce_after_minutes=cfg.replay_announce_after_minutes,
            )
        self._cache = cache

    async def post(
        self,
        discord_user_id: int,
        embed: discord.Embed,
        *,
        replay_gap_minutes: int | None = None,
    ) -> None:
        """Post an embed to every configured guild that the user is a member of.

        The bot never pings users — posts go out as embed-only messages with
        all mention types suppressed.

        `replay_gap_minutes` is set for "user resumed an old game" session
        events. A guild only receives the post if its configured threshold is
        non-zero and ≤ the gap.
        """
        if not self._cache:
            await self.refresh()

        targets: list[discord.TextChannel] = []
        skipped_no_member = 0
        for guild in self._bot.guilds:
            entry = self._cache.get(guild.id)
            if entry is None:
                continue
            if replay_gap_minutes is not None:
                threshold = entry.replay_announce_after_minutes
                if threshold <= 0 or threshold > replay_gap_minutes:
                    continue
            member = guild.get_member(discord_user_id)
            if member is None:
                skipped_no_member += 1
                continue
            channel = guild.get_channel(entry.channel_id)
            if isinstance(channel, discord.TextChannel):
                targets.append(channel)

        if not targets:
            # Silent skip used to be the prime suspect for "DB updates, channel
            # quiet". Log it so the next regression is obvious in the logs.
            log.info(
                "dispatcher.post: no targets for discord_user_id=%s "
                "(guilds=%s, configured=%s, missing_member=%s, replay_gap=%s)",
                discord_user_id,
                len(self._bot.guilds),
                sum(1 for v in self._cache.values() if v is not None),
                skipped_no_member,
                replay_gap_minutes,
            )
            return

        allowed = discord.AllowedMentions.none()
        for channel in targets:
            try:
                await channel.send(embed=embed, allowed_mentions=allowed)
            except discord.Forbidden:
                log.warning(
                    "Cannot post to #%s in %s: missing View Channel, Send Messages, or Embed Links",
                    channel,
                    channel.guild,
                )
            except discord.DiscordException:
                log.exception("Failed to post to %s in %s", channel, channel.guild)

    async def broadcast(
        self,
        embed: discord.Embed,
    ) -> list[discord.Message]:
        """Post an embed to every configured guild's announce channel.

        Unlike `post`, this is not scoped to a specific user — it's for
        server-wide announcements (e.g. Achievement of the Week). Returns the
        sent `discord.Message` objects so callers can pin or follow up.
        """
        if not self._cache:
            await self.refresh()

        allowed = discord.AllowedMentions.none()
        sent: list[discord.Message] = []
        for guild in self._bot.guilds:
            entry = self._cache.get(guild.id)
            if entry is None:
                continue
            channel = guild.get_channel(entry.channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                msg = await channel.send(embed=embed, allowed_mentions=allowed)
                sent.append(msg)
            except discord.Forbidden:
                log.warning(
                    "Cannot broadcast to #%s in %s: missing View Channel, Send Messages, or Embed Links",
                    channel,
                    channel.guild,
                )
            except discord.DiscordException:
                log.exception("Failed to broadcast to %s in %s", channel, channel.guild)
        return sent
