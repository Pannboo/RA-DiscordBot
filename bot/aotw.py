"""Achievement-of-the-Week presence updater.

Drives the bot's own Discord status with the current AOTW. Two cooperating
loops:

  * `_refresh_loop` — fetches the AOTW once per week, exactly when the next
    rollover lands (computed from RA's `StartAt`). One API call per week.

  * `_rotate_loop` — alternates the bot's presence every ~30s between the
    achievement title and the game title. Custom activities are used so
    Discord doesn't prepend "Watching" / "Playing" — that saves about 9
    characters in the user-list truncation window.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from sqlalchemy import delete, select

from bot.db import get_session
from bot.db.models import AOTWPin, AOTWState
from bot.embeds.aotw import aotw_embed
from bot.poller.dispatcher import Dispatcher
from bot.ra.client import RAClient

log = logging.getLogger(__name__)

# Refresh cadence. The normal cadence is computed from RA's StartAt field —
# we refresh shortly after the next weekly rollover.
FALLBACK_REFRESH_SECONDS = 24 * 3600  # if StartAt is missing / unparseable
ERROR_RETRY_SECONDS = 3600  # if the API call itself fails
ROLLOVER_BUFFER = timedelta(minutes=5)  # cushion past the rollover instant

# Rotation cadence. Discord's presence rate limit is ~5/min, so 30s leaves
# plenty of headroom while still feeling lively in the member list.
ROTATE_INTERVAL_SECONDS = 30
ACTIVITY_MAX_LEN = 128


class AOTWPresence:
    def __init__(
        self,
        bot: discord.Client,
        ra: RAClient,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self._bot = bot
        self._ra = ra
        self._dispatcher = dispatcher
        self._refresh_task: asyncio.Task | None = None
        self._rotate_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._statuses: list[discord.BaseActivity] = []
        self._idx = 0

    # ---- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._stop.clear()
        self._refresh_task = asyncio.create_task(self._refresh_loop(), name="aotw-refresh")
        self._rotate_task = asyncio.create_task(self._rotate_loop(), name="aotw-rotate")

    async def stop(self) -> None:
        self._stop.set()
        for t in (self._refresh_task, self._rotate_task):
            if t is None:
                continue
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._refresh_task = None
        self._rotate_task = None

    # ---- loops --------------------------------------------------------------

    async def _refresh_loop(self) -> None:
        await self._bot.wait_until_ready()
        log.info("aotw refresh loop started")
        while not self._stop.is_set():
            delay = await self._fetch_and_update()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _rotate_loop(self) -> None:
        await self._bot.wait_until_ready()
        log.info("aotw rotate loop started (interval=%ss)", ROTATE_INTERVAL_SECONDS)
        while not self._stop.is_set():
            if self._statuses:
                activity = self._statuses[self._idx % len(self._statuses)]
                self._idx += 1
                try:
                    await self._bot.change_presence(activity=activity)
                except Exception:
                    log.exception("change_presence failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=ROTATE_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    # ---- refresh ------------------------------------------------------------

    async def _fetch_and_update(self) -> float:
        """Pull the current AOTW, rebuild the status list, set first status
        immediately, and return the seconds until the next refresh."""
        try:
            data = await self._ra.get_achievement_of_the_week()
        except Exception:
            log.exception("aotw fetch failed; retrying in 1h")
            return ERROR_RETRY_SECONDS
        if not data:
            log.warning("aotw endpoint returned no data; retrying in 1h")
            return ERROR_RETRY_SECONDS

        achievement = data.get("Achievement") or {}
        game = data.get("Game") or {}
        ach_title = (achievement.get("Title") or "").strip()
        game_title = (game.get("Title") or "").strip()

        statuses: list[discord.BaseActivity] = []
        if ach_title:
            statuses.append(
                discord.CustomActivity(name=f"🏅 {ach_title}"[:ACTIVITY_MAX_LEN])
            )
        if game_title:
            statuses.append(
                discord.CustomActivity(name=f"🎮 {game_title} (AOTW)"[:ACTIVITY_MAX_LEN])
            )

        if not statuses:
            log.warning("aotw payload had neither title nor game; retrying in 1h")
            return ERROR_RETRY_SECONDS

        self._statuses = statuses
        self._idx = 0
        # Show the first status right away so we don't sit on stale data
        # while waiting for the rotate loop's next tick.
        try:
            await self._bot.change_presence(activity=statuses[0])
            self._idx = 1
        except Exception:
            log.exception("initial change_presence failed")

        log.info("aotw refreshed: %s / %s", ach_title, game_title)

        # Broadcast + pin only when the achievement actually changed.
        ach_id = (achievement.get("ID") if isinstance(achievement, dict) else None)
        if self._dispatcher is not None and ach_id:
            try:
                await self._maybe_broadcast(int(ach_id), data)
            except Exception:
                log.exception("aotw broadcast failed")

        return self._delay_until_next_rollover(data.get("StartAt"))

    async def _maybe_broadcast(self, ach_id: int, payload: dict) -> None:
        """Broadcast + pin if this AOTW is new (different achievement_id from last state)."""
        async with get_session() as session:
            state = await session.get(AOTWState, 1)
            if state is not None and state.achievement_id == ach_id:
                return  # already announced

        embed = aotw_embed(payload)
        assert self._dispatcher is not None
        messages = await self._dispatcher.broadcast(embed)
        if not messages:
            log.info("aotw broadcast had no targets; skipping pin/state update")
            return

        # Unpin previous AOTW posts per guild, then pin the new one.
        async with get_session() as session:
            previous_pins = (
                (await session.execute(select(AOTWPin))).scalars().all()
            )
        prev_by_guild = {p.guild_id: p for p in previous_pins}

        for msg in messages:
            if msg.guild is None:
                continue
            prev = prev_by_guild.get(msg.guild.id)
            if prev is not None:
                await self._unpin_previous(msg.guild, prev)
            try:
                await msg.pin(reason="Achievement of the Week")
            except discord.Forbidden:
                log.warning(
                    "Cannot pin AOTW in #%s (%s): missing Manage Messages",
                    msg.channel,
                    msg.guild,
                )
            except discord.DiscordException:
                log.exception("Failed to pin AOTW in %s", msg.guild)

        # Persist new state and per-guild pins.
        async with get_session() as session:
            state = await session.get(AOTWState, 1)
            if state is None:
                session.add(
                    AOTWState(
                        id=1,
                        achievement_id=ach_id,
                        posted_at=datetime.now(timezone.utc),
                    )
                )
            else:
                state.achievement_id = ach_id
                state.posted_at = datetime.now(timezone.utc)

            # Replace pin records for guilds we just posted to.
            guild_ids = [msg.guild.id for msg in messages if msg.guild is not None]
            if guild_ids:
                await session.execute(
                    delete(AOTWPin).where(AOTWPin.guild_id.in_(guild_ids))
                )
            for msg in messages:
                if msg.guild is None:
                    continue
                session.add(
                    AOTWPin(
                        guild_id=msg.guild.id,
                        channel_id=msg.channel.id,
                        message_id=msg.id,
                        achievement_id=ach_id,
                    )
                )
            await session.commit()

        log.info("aotw broadcast to %d guild(s), pinned new message", len(messages))

    async def _unpin_previous(self, guild: discord.Guild, pin: AOTWPin) -> None:
        channel = guild.get_channel(pin.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(pin.message_id)
        except discord.NotFound:
            return
        except discord.Forbidden:
            log.warning("Cannot fetch previous AOTW message in #%s (%s)", channel, guild)
            return
        except discord.DiscordException:
            log.exception("Failed to fetch previous AOTW message in %s", guild)
            return
        try:
            await msg.unpin(reason="Rotating Achievement of the Week")
        except discord.Forbidden:
            log.warning("Cannot unpin previous AOTW in #%s (%s)", channel, guild)
        except discord.DiscordException:
            log.exception("Failed to unpin previous AOTW in %s", guild)

    @staticmethod
    def _delay_until_next_rollover(start_at_str: str | None) -> float:
        """RA serves `StartAt` as ISO 8601 UTC. The next AOTW lands exactly
        7 days later — add a small buffer so we don't race the flip."""
        if not start_at_str:
            return FALLBACK_REFRESH_SECONDS
        try:
            start_at = datetime.fromisoformat(start_at_str.replace("Z", "+00:00"))
        except ValueError:
            log.warning("couldn't parse StartAt=%r; using fallback cadence", start_at_str)
            return FALLBACK_REFRESH_SECONDS

        next_at = start_at + timedelta(days=7) + ROLLOVER_BUFFER
        delay = (next_at - datetime.now(timezone.utc)).total_seconds()
        if delay < 60:
            # RA's clock ahead of ours, or we restarted right on the boundary —
            # don't busy-spin.
            return ERROR_RETRY_SECONDS
        log.info(
            "next AOTW refresh in %.1fh (at %s)", delay / 3600, next_at.isoformat()
        )
        return delay
