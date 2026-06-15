"""Paginated, ephemeral view of every achievement in a game set.

Page 0 is the game card (set info + your progress); pages 1..N are one
achievement each, so Discord can render each badge as the embed thumbnail.
The select dropdown lets the user jump anywhere within a 25-achievement
window of the current position. Owned by the invoking user.
"""

from __future__ import annotations

import discord

from bot.embeds.game import game_progress_embed, unlinked_game_embed
from bot.ra.client import achievement_url, badge_url, game_icon_url, game_url
from bot.ra.consoles import format_console
from bot.ra.types import AchievementInfo, GameInfoProgress
from bot.ui.common import OwnedView

ACHIEVEMENT_COLOR = 0xCFB53B
SELECT_WINDOW = 25  # Discord's per-select option cap


def _achievement_embed(
    game: GameInfoProgress,
    achievement: AchievementInfo,
    index: int,
    total: int,
    ra_username: str | None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏅  {achievement.title or 'Untitled achievement'}",
        url=achievement_url(achievement.id) if achievement.id else None,
        description=f"> *{achievement.description or 'No description.'}*",
        color=ACHIEVEMENT_COLOR,
    )
    if achievement.badge_name:
        embed.set_thumbnail(url=badge_url(achievement.badge_name))

    points_value = f"**{achievement.points}** pts"
    if achievement.true_ratio:
        points_value += f"\n💎 {achievement.true_ratio} RetroPoints"
    embed.add_field(name="Points", value=points_value, inline=True)

    if achievement.type:
        pretty = achievement.type.replace("_", " ").title()
        embed.add_field(name="Type", value=pretty, inline=True)

    if achievement.num_awarded:
        rate = ""
        if achievement.num_awarded_hardcore:
            rate = f" ({achievement.num_awarded_hardcore:,} HC)"
        embed.add_field(
            name="Earned by",
            value=f"{achievement.num_awarded:,} players{rate}",
            inline=True,
        )

    if ra_username:
        if achievement.date_earned_hardcore is not None:
            unlocked = (
                f"🔥 Hardcore — "
                f"{discord.utils.format_dt(achievement.date_earned_hardcore, 'R')}"
            )
        elif achievement.date_earned is not None:
            unlocked = (
                f"💧 Softcore — "
                f"{discord.utils.format_dt(achievement.date_earned, 'R')}"
            )
        else:
            unlocked = "🔒 Not yet unlocked"
        embed.add_field(name=f"{ra_username}", value=unlocked, inline=False)

    footer = (
        f"{game.title} • {game.console_name} • "
        f"Achievement {index} of {total}"
    )
    if achievement.author:
        footer += f" • set by {achievement.author}"
    embed.set_footer(
        text=footer,
        icon_url=game_icon_url(game.image_icon) if game.image_icon else None,
    )
    return embed


class _JumpSelect(discord.ui.Select["AchievementsPagerView"]):
    def __init__(self, view: "AchievementsPagerView") -> None:
        super().__init__(
            placeholder="Jump to achievement…",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="placeholder", value="0")],
            row=1,
        )
        self._owner_view = view

    def refresh(self) -> None:
        view = self._owner_view
        achievements = view.achievements
        total = len(achievements)
        if total == 0:
            self.options = [
                discord.SelectOption(label="No achievements", value="0", default=True)
            ]
            self.disabled = True
            return
        self.disabled = False
        # Window is 1-indexed against achievements (page 0 is the game card).
        # Center the window around the current achievement index, clamped.
        current_ach_idx = max(0, view.page - 1)
        half = SELECT_WINDOW // 2
        start = max(0, min(current_ach_idx - half, total - SELECT_WINDOW))
        start = max(0, start)
        end = min(total, start + SELECT_WINDOW)
        options: list[discord.SelectOption] = []
        for i in range(start, end):
            ach = achievements[i]
            label = f"{i + 1}. {ach.title or 'Untitled'}"
            if len(label) > 100:
                label = label[:97] + "…"
            desc = ach.description or ""
            if len(desc) > 100:
                desc = desc[:97] + "…"
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(i + 1),  # +1 because page 0 = game card
                    description=desc or None,
                    default=(view.page == i + 1),
                )
            )
        self.options = options
        self.placeholder = (
            "Jump to achievement…"
            if total <= SELECT_WINDOW
            else f"Jump to achievement… ({start + 1}–{end} of {total})"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._owner_view.go_to(interaction, int(self.values[0]))


class AchievementsPagerView(OwnedView):
    """Pager over [game card, ach #1, ach #2, …]. `page == 0` is the game card."""

    def __init__(
        self,
        game: GameInfoProgress,
        ra_username: str | None,
        *,
        owner_id: int,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(owner_id=owner_id, timeout=timeout)
        self.game = game
        self.achievements: list[AchievementInfo] = sorted(
            game.achievements,
            key=lambda a: (a.display_order, a.id),
        )
        self.ra_username = ra_username
        self.page = 0  # 0 = game card, 1..N = achievement index +1

        self._first = discord.ui.Button(
            label="⏮", style=discord.ButtonStyle.secondary, row=0
        )
        self._prev = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, row=0
        )
        self._counter = discord.ui.Button(
            label="Game", style=discord.ButtonStyle.secondary, row=0, disabled=True
        )
        self._next = discord.ui.Button(
            label="Next ▶", style=discord.ButtonStyle.primary, row=0
        )
        self._last = discord.ui.Button(
            label="⏭", style=discord.ButtonStyle.secondary, row=0
        )
        self._open_game = discord.ui.Button(
            label="Open on RA",
            style=discord.ButtonStyle.link,
            url=game_url(game.game_id),
            row=2,
        )

        self._first.callback = self._on_first  # type: ignore[assignment]
        self._prev.callback = self._on_prev  # type: ignore[assignment]
        self._next.callback = self._on_next  # type: ignore[assignment]
        self._last.callback = self._on_last  # type: ignore[assignment]

        self.add_item(self._first)
        self.add_item(self._prev)
        self.add_item(self._counter)
        self.add_item(self._next)
        self.add_item(self._last)

        self._select = _JumpSelect(self)
        self.add_item(self._select)
        self.add_item(self._open_game)

        self._refresh_state()

    @property
    def _last_page(self) -> int:
        return len(self.achievements)  # max page index

    def _refresh_state(self) -> None:
        last = self._last_page
        if self.page == 0:
            self._counter.label = (
                f"Game · 0 / {last}" if last else "Game"
            )
        else:
            self._counter.label = f"{self.page} / {last}"
        self._first.disabled = self.page <= 0
        self._prev.disabled = self.page <= 0
        self._next.disabled = self.page >= last
        self._last.disabled = self.page >= last
        self._select.refresh()

    def current_embed(self) -> discord.Embed:
        if self.page == 0:
            if self.ra_username:
                return game_progress_embed(self.game, self.ra_username)
            return unlinked_game_embed(self.game)
        if not self.achievements:
            embed = discord.Embed(
                title=self.game.title,
                description="This game has no achievements in its set yet.",
                color=ACHIEVEMENT_COLOR,
            )
            if self.game.console_name:
                embed.add_field(name="Console", value=format_console(self.game.console_name))
            return embed
        ach = self.achievements[self.page - 1]
        return _achievement_embed(
            self.game,
            ach,
            self.page,
            len(self.achievements),
            self.ra_username,
        )

    async def go_to(self, interaction: discord.Interaction, new_page: int) -> None:
        last = self._last_page
        self.page = max(0, min(new_page, last))
        self._refresh_state()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _on_first(self, interaction: discord.Interaction) -> None:
        await self.go_to(interaction, 0)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        await self.go_to(interaction, self.page - 1)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await self.go_to(interaction, self.page + 1)

    async def _on_last(self, interaction: discord.Interaction) -> None:
        await self.go_to(interaction, self._last_page)
