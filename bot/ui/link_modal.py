from __future__ import annotations

from datetime import datetime, timezone

import discord
from sqlalchemy import select

from bot.db import get_session
from bot.db.models import User
from bot.ra.client import RAClient


class LinkModal(discord.ui.Modal, title="Link RetroAchievements"):
    ra_username: discord.ui.TextInput = discord.ui.TextInput(
        label="RetroAchievements username",
        placeholder="e.g. Scott",
        min_length=2,
        max_length=32,
        required=True,
    )

    def __init__(self, ra: RAClient, on_done=None) -> None:
        """`on_done(interaction)` is awaited after a successful link so the caller
        can refresh whatever panel they came from."""
        super().__init__()
        self._ra = ra
        self._on_done = on_done

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        username = str(self.ra_username.value).strip()

        summary = await self._ra.get_user_summary(username, recent_games=0)
        if summary is None:
            await interaction.followup.send(
                f"Couldn't find a RetroAchievements user named **{username}**. "
                "Check the spelling and try again.",
                ephemeral=True,
            )
            return

        async with get_session() as session:
            existing = await session.get(User, interaction.user.id)
            other = await session.scalar(
                select(User).where(
                    User.ra_username == summary.user,
                    User.discord_id != interaction.user.id,
                )
            )
            if other is not None:
                await interaction.followup.send(
                    f"**{summary.user}** is already linked to another Discord account.",
                    ephemeral=True,
                )
                return

            now = datetime.now(timezone.utc)
            if existing is None:
                session.add(
                    User(
                        discord_id=interaction.user.id,
                        ra_username=summary.user,
                        linked_at=now,
                        next_poll_at=now,
                        total_points=summary.total_points,
                        total_softcore_points=summary.total_softcore_points,
                        total_retro_points=summary.total_true_points,
                    )
                )
            else:
                existing.ra_username = summary.user
                existing.next_poll_at = now
                existing.total_points = summary.total_points
                existing.total_softcore_points = summary.total_softcore_points
                existing.total_retro_points = summary.total_true_points
            await session.commit()

        await interaction.followup.send(
            f"Linked to **{summary.user}**  "
            f"({summary.total_points:,} HC pts · {summary.total_softcore_points:,} SC pts).",
            ephemeral=True,
        )
        if self._on_done is not None:
            await self._on_done(interaction)
