from __future__ import annotations

import discord

from bot.db import get_session
from bot.db.models import User
from bot.ui.common import OwnedView


async def build_settings_embed(discord_user_id: int) -> discord.Embed:
    async with get_session() as session:
        user = await session.get(User, discord_user_id)
    if user is None:
        return discord.Embed(
            title="Settings",
            description="Link an account first via `/ra` → **Link Account**.",
            color=0xCFB53B,
        )
    hc = "Hardcore only" if user.notify_hardcore_only else "Hardcore + Softcore"
    embed = discord.Embed(
        title=f"Settings — {user.ra_username}",
        description=(
            "Tweak how your achievements get posted. Changes apply immediately."
        ),
        color=0xCFB53B,
    )
    embed.add_field(name="Modes posted", value=hc, inline=True)
    return embed


class SettingsView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=300)

    async def _toggle(
        self, interaction: discord.Interaction, attr: str
    ) -> None:
        async with get_session() as session:
            user = await session.get(User, self._owner_id)
            if user is None:
                await interaction.response.send_message(
                    "You haven't linked an account.", ephemeral=True
                )
                return
            current = getattr(user, attr)
            setattr(user, attr, not current)
            await session.commit()
        embed = await build_settings_embed(self._owner_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Toggle Hardcore-only", style=discord.ButtonStyle.primary, emoji="🔒"
    )
    async def toggle_hardcore(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._toggle(interaction, "notify_hardcore_only")
