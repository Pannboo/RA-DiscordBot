from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from bot.aotw import AOTWPresence
from bot.commands import register as register_commands
from bot.config import get_settings
from bot.db import init_engine, shutdown_engine
from bot.poller import Dispatcher, PollScheduler, PresenceTracker
from bot.ra.client import RAClient
from bot.ra.consoles import set_consoles

log = logging.getLogger("ra_bot")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.presences = True  # required to detect "playing X" activity
    intents.members = True  # required to resolve guild membership for dispatch
    intents.message_content = False
    return intents


class RABot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!unused", intents=_build_intents())
        self.ra = RAClient()
        self.dispatcher_ = Dispatcher(self)
        self.scheduler = PollScheduler(self.ra, self.dispatcher_)
        self.presence = PresenceTracker(self)
        self.aotw = AOTWPresence(self, self.ra, self.dispatcher_)

    async def setup_hook(self) -> None:
        init_engine()
        register_commands(self.tree, self.ra, self.dispatcher_)
        self.presence.install()

        # Cache the RA console list so embed renderers can build /system/<id> URLs.
        try:
            consoles = await self.ra.get_console_ids()
            set_consoles({c["Name"]: c["ID"] for c in consoles if "Name" in c and "ID" in c})
            log.info("Loaded %d RA consoles", len(consoles))
        except Exception:
            log.exception("Failed to load RA console list — links will fall back to plain text")

        settings = get_settings()
        dev_guilds = settings.dev_guild_id_list
        if dev_guilds:
            for gid in dev_guilds:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            log.info("Synced commands to dev guilds: %s", dev_guilds)
        else:
            await self.tree.sync()
            log.info("Synced global commands (propagation may take up to an hour)")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s) in %d guild(s)", self.user, self.user.id, len(self.guilds))
        await self.dispatcher_.refresh()
        if not self.scheduler._tasks:  # idempotent on reconnect
            self.scheduler.start()
        self.aotw.start()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild %s (id=%s)", guild.name, guild.id)
        await self.dispatcher_.refresh()

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Left guild %s (id=%s)", guild.name, guild.id)
        await self.dispatcher_.refresh()

    async def close(self) -> None:
        await self.aotw.stop()
        await self.scheduler.stop()
        await self.ra.aclose()
        await shutdown_engine()
        await super().close()


async def _amain() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    bot = RABot()
    async with bot:
        await bot.start(settings.discord_token)


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        log.info("interrupted")


if __name__ == "__main__":
    main()
