from __future__ import annotations

import discord

from bot.db import get_session
from bot.db.models import GuildConfig
from bot.poller import Dispatcher
from bot.ra.client import RAClient
from bot.ui.admin_user import AdminUserPickerView
from bot.ui.common import OwnedView


async def build_admin_embed(guild: discord.Guild) -> discord.Embed:
    async with get_session() as session:
        cfg = await session.get(GuildConfig, guild.id)
    embed = discord.Embed(
        title=f"RetroAchievements — {guild.name}",
        description=(
            "Pick the channel where unlocks, awards, sessions, and milestones should appear.\n"
            "Use the toggle to pause posting without losing the channel choice."
        ),
        color=0xCFB53B,
    )
    if cfg is None or cfg.announce_channel_id is None:
        embed.add_field(name="Announce channel", value="*not set*", inline=True)
        embed.add_field(name="Status", value="🔴 disabled", inline=True)
    else:
        embed.add_field(
            name="Announce channel",
            value=f"<#{cfg.announce_channel_id}>",
            inline=True,
        )
        embed.add_field(
            name="Status",
            value="🟢 enabled" if cfg.enabled else "⏸️ paused",
            inline=True,
        )
    return embed


class AdminHubView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        guild: discord.Guild,
        dispatcher: Dispatcher,
        ra: RAClient,
    ) -> None:
        super().__init__(owner_id, timeout=300)
        self._guild = guild
        self._dispatcher = dispatcher
        self._ra = ra
        # Toggle button label depends on current state — build dynamically.
        self.toggle_button = discord.ui.Button(
            label="Loading…", style=discord.ButtonStyle.secondary, row=1
        )
        self.toggle_button.callback = self._on_toggle  # type: ignore[assignment]
        self.add_item(self.toggle_button)
        self.refresh_button = discord.ui.Button(
            label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=1
        )
        self.refresh_button.callback = self._on_refresh  # type: ignore[assignment]
        self.add_item(self.refresh_button)
        self.manage_button = discord.ui.Button(
            label="Manage user", style=discord.ButtonStyle.primary, emoji="👥", row=1
        )
        self.manage_button.callback = self._on_manage  # type: ignore[assignment]
        self.add_item(self.manage_button)

    @classmethod
    async def create(
        cls,
        owner_id: int,
        guild: discord.Guild,
        dispatcher: Dispatcher,
        ra: RAClient,
    ) -> "AdminHubView":
        view = cls(owner_id, guild, dispatcher, ra)
        await view._sync_toggle_label()
        return view

    async def _sync_toggle_label(self) -> None:
        async with get_session() as session:
            cfg = await session.get(GuildConfig, self._guild.id)
        if cfg is None or cfg.announce_channel_id is None:
            self.toggle_button.label = "Set a channel first"
            self.toggle_button.disabled = True
            self.toggle_button.style = discord.ButtonStyle.secondary
        elif cfg.enabled:
            self.toggle_button.label = "Pause posting"
            self.toggle_button.disabled = False
            self.toggle_button.style = discord.ButtonStyle.danger
        else:
            self.toggle_button.label = "Resume posting"
            self.toggle_button.disabled = False
            self.toggle_button.style = discord.ButtonStyle.success

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Choose the announce channel…",
        min_values=1,
        max_values=1,
    )
    async def channel_select(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ) -> None:
        choice = select.values[0]
        # ChannelSelect returns a `discord.app_commands.AppCommandChannel`; resolve to a TextChannel.
        channel = self._guild.get_channel(choice.id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "That channel isn't a text channel I can post to.", ephemeral=True
            )
            return
        me = self._guild.me
        perms = channel.permissions_for(me) if me else None
        if perms is None or not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"I need **Send Messages** and **Embed Links** in {channel.mention}.",
                ephemeral=True,
            )
            return

        async with get_session() as session:
            cfg = await session.get(GuildConfig, self._guild.id)
            if cfg is None:
                session.add(
                    GuildConfig(
                        guild_id=self._guild.id,
                        announce_channel_id=channel.id,
                        enabled=True,
                    )
                )
            else:
                cfg.announce_channel_id = channel.id
                cfg.enabled = True
            await session.commit()
        await self._dispatcher.refresh()
        await self._sync_toggle_label()
        embed = await build_admin_embed(self._guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_toggle(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            cfg = await session.get(GuildConfig, self._guild.id)
            if cfg is None or cfg.announce_channel_id is None:
                await interaction.response.send_message(
                    "Pick a channel first.", ephemeral=True
                )
                return
            cfg.enabled = not cfg.enabled
            await session.commit()
        await self._dispatcher.refresh()
        await self._sync_toggle_label()
        embed = await build_admin_embed(self._guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        await self._sync_toggle_label()
        embed = await build_admin_embed(self._guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_manage(self, interaction: discord.Interaction) -> None:
        picker = AdminUserPickerView(self._owner_id, self._ra, self._guild)
        embed = discord.Embed(
            title="Manage a user's RA account",
            description=(
                "Pick a Discord user below. You'll then be able to link or unlink "
                "their RetroAchievements account on their behalf."
            ),
            color=0xCFB53B,
        )
        await interaction.response.send_message(
            embed=embed, view=picker, ephemeral=True
        )
        picker.message = await interaction.original_response()
