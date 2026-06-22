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

    game_public = cfg is not None and cfg.game_lookup_public
    embed.add_field(
        name="`/ra game` replies",
        value=(
            "🌐 public (only the invoker can drive the buttons)"
            if game_public
            else "🔒 ephemeral (only the invoker can see)"
        ),
        inline=False,
    )
    replay_after = cfg.replay_announce_after_minutes if cfg is not None else 360
    embed.add_field(
        name="Re-announce on resume",
        value=(
            "🚫 off — only switching games announces"
            if replay_after <= 0
            else f"⏱️ after **{_format_minutes(replay_after)}** away from a game"
        ),
        inline=False,
    )
    return embed


_REPLAY_PRESETS: list[tuple[int, str]] = [
    (0, "Off"),
    (60, "1 hour"),
    (180, "3 hours"),
    (360, "6 hours"),
    (720, "12 hours"),
    (1440, "24 hours"),
]


def _format_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, rem = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if rem == 0 else f"{hours}h {rem}m"
    days, rem_h = divmod(hours, 24)
    return f"{days}d" if rem_h == 0 else f"{days}d {rem_h}h"


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
        # Row 2: per-feature toggles.
        self.game_visibility_button = discord.ui.Button(
            label="Loading…", style=discord.ButtonStyle.secondary, row=2
        )
        self.game_visibility_button.callback = self._on_toggle_game_visibility  # type: ignore[assignment]
        self.add_item(self.game_visibility_button)
        # Row 3: re-announce-on-resume threshold.
        self.replay_select = discord.ui.Select(
            placeholder="Re-announce when a user resumes a game after…",
            min_values=1,
            max_values=1,
            row=3,
            options=[
                discord.SelectOption(label=label, value=str(value))
                for value, label in _REPLAY_PRESETS
            ],
        )
        self.replay_select.callback = self._on_replay_select  # type: ignore[assignment]
        self.add_item(self.replay_select)

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

        # /ra game visibility toggle — independent of the announce channel.
        game_public = cfg is not None and cfg.game_lookup_public
        if game_public:
            self.game_visibility_button.label = "Make /ra game private"
            self.game_visibility_button.style = discord.ButtonStyle.secondary
            self.game_visibility_button.emoji = "🔒"
        else:
            self.game_visibility_button.label = "Make /ra game public"
            self.game_visibility_button.style = discord.ButtonStyle.primary
            self.game_visibility_button.emoji = "🌐"

        # Highlight the current re-announce threshold in the select.
        current_replay = cfg.replay_announce_after_minutes if cfg is not None else 360
        for option in self.replay_select.options:
            option.default = int(option.value) == current_replay

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

    async def _on_toggle_game_visibility(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            cfg = await session.get(GuildConfig, self._guild.id)
            if cfg is None:
                # No row yet — create one with the toggle flipped on, even
                # without an announce channel. /ra game doesn't depend on it.
                session.add(
                    GuildConfig(
                        guild_id=self._guild.id,
                        announce_channel_id=None,
                        enabled=False,
                        game_lookup_public=True,
                    )
                )
            else:
                cfg.game_lookup_public = not cfg.game_lookup_public
            await session.commit()
        await self._sync_toggle_label()
        embed = await build_admin_embed(self._guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_replay_select(self, interaction: discord.Interaction) -> None:
        minutes = int(self.replay_select.values[0])
        async with get_session() as session:
            cfg = await session.get(GuildConfig, self._guild.id)
            if cfg is None:
                session.add(
                    GuildConfig(
                        guild_id=self._guild.id,
                        announce_channel_id=None,
                        enabled=False,
                        replay_announce_after_minutes=minutes,
                    )
                )
            else:
                cfg.replay_announce_after_minutes = minutes
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
