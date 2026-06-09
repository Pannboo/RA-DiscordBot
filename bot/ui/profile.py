from __future__ import annotations

from typing import Literal

import discord

from bot.ra.client import RAClient, badge_url, game_icon_url, game_url, user_pic_url, user_url
from bot.ui.common import OwnedView

Tab = Literal["overview", "recent", "awards"]


async def build_profile_embed(ra: RAClient, username: str, tab: Tab) -> discord.Embed:
    if tab == "overview":
        return await _overview_embed(ra, username)
    if tab == "recent":
        return await _recent_embed(ra, username)
    return await _awards_embed(ra, username)


async def _overview_embed(ra: RAClient, username: str) -> discord.Embed:
    summary = await ra.get_user_summary(username, recent_games=1)
    if summary is None:
        return discord.Embed(
            title=f"{username}",
            description="User not found on RetroAchievements.",
            color=0xE74C3C,
        )
    embed = discord.Embed(
        title=summary.user,
        url=user_url(summary.user),
        description=summary.motto or "",
        color=0xCFB53B,
    )
    if summary.user_pic:
        embed.set_thumbnail(url=user_pic_url(summary.user_pic))
    embed.add_field(name="Hardcore", value=f"{summary.total_points:,} pts", inline=True)
    embed.add_field(name="Softcore", value=f"{summary.total_softcore_points:,} pts", inline=True)
    embed.add_field(name="RetroPoints", value=f"{summary.total_true_points:,}", inline=True)
    if summary.rank:
        embed.add_field(name="Rank", value=f"#{summary.rank:,}", inline=True)
    if summary.rich_presence_msg:
        embed.add_field(name="Currently", value=summary.rich_presence_msg, inline=False)
    embed.set_footer(text="Overview")
    return embed


async def _recent_embed(ra: RAClient, username: str) -> discord.Embed:
    recent = await ra.get_user_recent_achievements(username, minutes=60 * 24 * 7)
    embed = discord.Embed(
        title=f"{username} — Recent Unlocks",
        url=user_url(username),
        color=0xCFB53B,
    )
    if not recent:
        embed.description = "No achievements unlocked in the last 7 days."
        embed.set_footer(text="Recent Unlocks")
        return embed
    # Show up to 10 most recent.
    recent_sorted = sorted(recent, key=lambda a: a.date, reverse=True)[:10]
    lines: list[str] = []
    for a in recent_sorted:
        icon = "🔒" if a.is_hardcore else "🔓"
        when = discord.utils.format_dt(a.date, "R")
        lines.append(
            f"{icon} **[{a.title}]({game_url(a.game_id)})** "
            f"· {a.points} pts · *{a.game_title}* · {when}"
        )
    embed.description = "\n".join(lines)
    # Use the newest achievement's badge as a thumbnail.
    if recent_sorted[0].badge_name:
        embed.set_thumbnail(url=badge_url(recent_sorted[0].badge_name))
    embed.set_footer(text="Recent Unlocks (last 7 days)")
    return embed


async def _awards_embed(ra: RAClient, username: str) -> discord.Embed:
    awards = await ra.get_user_awards(username)
    embed = discord.Embed(
        title=f"{username} — Awards",
        url=user_url(username),
        color=0x9B59B6,
    )
    if awards is None:
        embed.description = "Couldn't fetch awards."
        return embed
    embed.add_field(name="Mastery", value=str(awards.mastery_awards_count), inline=True)
    embed.add_field(name="Completion", value=str(awards.completion_awards_count), inline=True)
    embed.add_field(
        name="Beaten (HC)",
        value=str(awards.beaten_hardcore_awards_count),
        inline=True,
    )
    embed.add_field(
        name="Beaten (SC)",
        value=str(awards.beaten_softcore_awards_count),
        inline=True,
    )
    embed.add_field(name="Total", value=str(awards.total_awards_count), inline=True)
    # Most recent 5 visible awards as a list.
    recent_awards = sorted(
        awards.visible_user_awards, key=lambda a: a.awarded_at, reverse=True
    )[:5]
    if recent_awards:
        lines = []
        for a in recent_awards:
            when = discord.utils.format_dt(a.awarded_at, "R")
            game = a.title or "Unknown"
            mode = " (HC)" if a.is_hardcore else ""
            lines.append(f"• **{a.award_type}{mode}** — {game} · {when}")
        embed.add_field(name="Recent", value="\n".join(lines), inline=False)
    if recent_awards and recent_awards[0].image_icon:
        embed.set_thumbnail(url=game_icon_url(recent_awards[0].image_icon))
    embed.set_footer(text="Awards")
    return embed


class ProfileView(OwnedView):
    def __init__(self, owner_id: int, ra: RAClient, username: str) -> None:
        super().__init__(owner_id, timeout=300)
        self._ra = ra
        self._username = username
        self.tab: Tab = "overview"

    @classmethod
    async def create(cls, owner_id: int, ra: RAClient, username: str) -> "ProfileView":
        view = cls(owner_id, ra, username)
        view._sync_button_state()
        return view

    def _sync_button_state(self) -> None:
        # Highlight the active tab; gray out the rest.
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id in {
                "tab_overview",
                "tab_recent",
                "tab_awards",
            }:
                active = child.custom_id == f"tab_{self.tab}"
                child.style = (
                    discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary
                )
                child.disabled = active

    async def _switch(self, interaction: discord.Interaction, tab: Tab) -> None:
        self.tab = tab
        self._sync_button_state()
        await interaction.response.defer()
        embed = await build_profile_embed(self._ra, self._username, tab)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Overview", emoji="📋", custom_id="tab_overview")
    async def tab_overview(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._switch(interaction, "overview")

    @discord.ui.button(label="Recent", emoji="✨", custom_id="tab_recent")
    async def tab_recent(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._switch(interaction, "recent")

    @discord.ui.button(label="Awards", emoji="🏆", custom_id="tab_awards")
    async def tab_awards(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._switch(interaction, "awards")
