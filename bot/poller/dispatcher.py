from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from bot.db import get_session
from bot.db.models import GuildConfig

log = logging.getLogger(__name__)


class Dispatcher:
    """Fans an event out to every guild the linked Discord user is a member of,
    posting to that guild's configured announce channel.
    """

    def __init__(self, bot: discord.Client) -> None:
        self._bot = bot
        # In-memory cache: guild_id -> announce_channel_id (or None if disabled).
        self._cache: dict[int, int | None] = {}

    async def refresh(self) -> None:
        async with get_session() as session:
            rows = (await session.execute(select(GuildConfig))).scalars().all()
        self._cache = {
            cfg.guild_id: (cfg.announce_channel_id if cfg.enabled else None) for cfg in rows
        }

    async def post(
        self,
        discord_user_id: int,
        embed: discord.Embed,
    ) -> None:
        """Post an embed to every configured guild that the user is a member of.

        The bot never pings users — posts go out as embed-only messages with
        all mention types suppressed.
        """
        if not self._cache:
            await self.refresh()

        targets: list[discord.TextChannel] = []
        for guild in self._bot.guilds:
            channel_id = self._cache.get(guild.id)
            if not channel_id:
                continue
            member = guild.get_member(discord_user_id)
            if member is None:
                continue
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                targets.append(channel)

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
            channel_id = self._cache.get(guild.id)
            if not channel_id:
                continue
            channel = guild.get_channel(channel_id)
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
