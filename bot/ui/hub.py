from __future__ import annotations

import discord
from sqlalchemy import delete

from bot.db import get_session
from bot.db.models import User
from bot.ra.client import RAClient
from bot.ui.common import ConfirmView, OwnedView
from bot.ui.link_modal import LinkModal


async def _load_user(discord_user_id: int) -> User | None:
    async with get_session() as session:
        return await session.get(User, discord_user_id)


async def build_hub_embed(discord_user_id: int) -> discord.Embed:
    user = await _load_user(discord_user_id)
    if user is None:
        return discord.Embed(
            title="RetroAchievements",
            description=(
                "You don't have an account linked yet.\n\n"
                "Click **Link Account** below to get started — you'll be asked for your "
                "RetroAchievements username, and we'll verify it before saving."
            ),
            color=0xCFB53B,
        )
    last_poll = (
        discord.utils.format_dt(user.last_polled_at, "R") if user.last_polled_at else "never"
    )
    embed = discord.Embed(
        title=f"RetroAchievements — {user.ra_username}",
        description=f"Last poll: {last_poll}",
        color=0xCFB53B,
    )
    embed.add_field(name="Hardcore", value=f"{user.total_points:,} pts", inline=True)
    embed.add_field(name="Softcore", value=f"{user.total_softcore_points:,} pts", inline=True)
    embed.add_field(name="RetroPoints", value=f"{user.total_retro_points:,}", inline=True)
    embed.set_footer(
        text="Hardcore only" if user.notify_hardcore_only else "Hardcore + Softcore"
    )
    return embed


class HubView(OwnedView):
    def __init__(self, owner_id: int, ra: RAClient) -> None:
        super().__init__(owner_id)
        self._ra = ra
        # Buttons created dynamically so we can vary labels based on link state.

    @classmethod
    async def create(cls, owner_id: int, ra: RAClient) -> "HubView":
        view = cls(owner_id, ra)
        user = await _load_user(owner_id)
        await view._rebuild(linked=user is not None)
        return view

    async def _rebuild(self, *, linked: bool) -> None:
        self.clear_items()

        link_btn = discord.ui.Button(
            label="Re-link Account" if linked else "Link Account",
            style=discord.ButtonStyle.primary,
            emoji="🔗",
        )
        link_btn.callback = self._on_link  # type: ignore[assignment]
        self.add_item(link_btn)

        profile_btn = discord.ui.Button(
            label="Profile", style=discord.ButtonStyle.secondary, emoji="👤", disabled=not linked
        )
        profile_btn.callback = self._on_profile  # type: ignore[assignment]
        self.add_item(profile_btn)

        settings_btn = discord.ui.Button(
            label="Settings",
            style=discord.ButtonStyle.secondary,
            emoji="⚙️",
            disabled=not linked,
        )
        settings_btn.callback = self._on_settings  # type: ignore[assignment]
        self.add_item(settings_btn)

        refresh_btn = discord.ui.Button(
            label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄"
        )
        refresh_btn.callback = self._on_refresh  # type: ignore[assignment]
        self.add_item(refresh_btn)

        unlink_btn = discord.ui.Button(
            label="Unlink",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            disabled=not linked,
            row=1,
        )
        unlink_btn.callback = self._on_unlink  # type: ignore[assignment]
        self.add_item(unlink_btn)

    async def _refresh_message(self, interaction: discord.Interaction | None = None) -> None:
        user = await _load_user(self._owner_id)
        await self._rebuild(linked=user is not None)
        embed = await build_hub_embed(self._owner_id)
        # Prefer editing through the live interaction (fast path) if it hasn't been used.
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
            return
        # Otherwise edit the original hub message via its bound webhook token.
        if self.message is not None:
            await self.message.edit(embed=embed, view=self)

    # ---- callbacks ----------------------------------------------------------

    async def _on_link(self, interaction: discord.Interaction) -> None:
        async def _after(_modal_interaction: discord.Interaction) -> None:
            # Modal's interaction is done; fall back to editing our bound message.
            await self._refresh_message()

        await interaction.response.send_modal(LinkModal(self._ra, on_done=_after))

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        await self._refresh_message(interaction)

    async def _on_profile(self, interaction: discord.Interaction) -> None:
        from bot.ui.profile import ProfileView, build_profile_embed

        user = await _load_user(self._owner_id)
        if user is None:
            await interaction.response.send_message(
                "Link an account first.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = await ProfileView.create(self._owner_id, self._ra, user.ra_username)
        embed = await build_profile_embed(self._ra, user.ra_username, view.tab)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def _on_settings(self, interaction: discord.Interaction) -> None:
        from bot.ui.settings import SettingsView, build_settings_embed

        user = await _load_user(self._owner_id)
        if user is None:
            await interaction.response.send_message(
                "Link an account first.", ephemeral=True
            )
            return
        view = SettingsView(self._owner_id)
        embed = await build_settings_embed(self._owner_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _on_unlink(self, interaction: discord.Interaction) -> None:
        confirm = ConfirmView(self._owner_id, confirm_label="Unlink", danger=True)
        await interaction.response.send_message(
            "Are you sure you want to unlink your RetroAchievements account? "
            "Your unlocks will stop posting until you link again.",
            view=confirm,
            ephemeral=True,
        )
        await confirm.wait()
        if not confirm.value:
            return
        async with get_session() as session:
            await session.execute(delete(User).where(User.discord_id == self._owner_id))
            await session.commit()
        # interaction.response was already used to send the confirm dialog — edit our bound message.
        await self._refresh_message()
