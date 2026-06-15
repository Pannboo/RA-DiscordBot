"""List-view achievement browser for `/ra game`.

The default view is a compact list of ~8 achievements with title, points and
your unlock state, so you can scan a whole set fast. Drill into any single
achievement via the "Open one…" dropdown to see its badge, full description
and award stats. A "Game card" button always brings you back to the set
overview. Ephemeral and owned by the invoking user.
"""

from __future__ import annotations

import discord

from bot.embeds.game import game_progress_embed, unlinked_game_embed
from bot.ra.client import achievement_url, badge_url, game_icon_url, game_url
from bot.ra.consoles import format_console
from bot.ra.types import AchievementInfo, GameInfoProgress
from bot.ui.common import OwnedView

ACHIEVEMENT_COLOR = 0xCFB53B
PAGE_SIZE = 8
LIST_DESC_TRUNCATE = 90
SELECT_LABEL_TRUNCATE = 97
SELECT_DESC_TRUNCATE = 97

MODE_GAME = "game"
MODE_LIST = "list"
MODE_DETAIL = "detail"


def _status_emoji(ach: AchievementInfo, linked: bool) -> str:
    if not linked:
        return "🏅"
    if ach.date_earned_hardcore is not None:
        return "🔥"
    if ach.date_earned is not None:
        return "💧"
    return "🔒"


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _list_embed(
    game: GameInfoProgress,
    achievements: list[AchievementInfo],
    page: int,
    page_count: int,
    ra_username: str | None,
) -> discord.Embed:
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(achievements))
    linked = ra_username is not None

    lines: list[str] = []
    for i in range(start, end):
        ach = achievements[i]
        status = _status_emoji(ach, linked)
        title = ach.title or "Untitled"
        head = f"{status} **{i + 1}. [{title}]({achievement_url(ach.id)})** — `{ach.points} pts`"
        if ach.true_ratio:
            head += f" • 💎 `{ach.true_ratio}`"
        desc = _truncate(ach.description or "", LIST_DESC_TRUNCATE)
        lines.append(head + (f"\n> {desc}" if desc else ""))

    embed = discord.Embed(
        title=f"{game.title} — Achievements",
        url=game_url(game.game_id),
        description="\n\n".join(lines) or "*This set has no achievements yet.*",
        color=ACHIEVEMENT_COLOR,
    )
    if game.image_icon:
        embed.set_thumbnail(url=game_icon_url(game.image_icon))

    summary_bits: list[str] = [f"{len(achievements)} in set"]
    if game.console_name:
        summary_bits.append(format_console(game.console_name))
    if linked:
        hc = sum(1 for a in achievements if a.date_earned_hardcore is not None)
        sc = sum(
            1
            for a in achievements
            if a.date_earned is not None and a.date_earned_hardcore is None
        )
        summary_bits.append(f"🔥 {hc} HC • 💧 {sc} SC")
    embed.add_field(name="​", value=" • ".join(summary_bits), inline=False)

    embed.set_footer(text=f"Page {page + 1} of {page_count}")
    return embed


def _detail_embed(
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
        embed.add_field(
            name="Type",
            value=achievement.type.replace("_", " ").title(),
            inline=True,
        )

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
        embed.add_field(name=ra_username, value=unlocked, inline=False)

    footer = f"{game.title} • {game.console_name} • Achievement {index + 1} of {total}"
    if achievement.author:
        footer += f" • set by {achievement.author}"
    embed.set_footer(
        text=footer,
        icon_url=game_icon_url(game.image_icon) if game.image_icon else None,
    )
    return embed


class _DrillSelect(discord.ui.Select["AchievementsPagerView"]):
    """Dropdown listing the achievements on the current list page.

    Selecting one switches the view into detail mode for that achievement.
    """

    def __init__(self, view: "AchievementsPagerView") -> None:
        super().__init__(
            placeholder="🔍 Open one…",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="placeholder", value="0")],
            row=1,
        )
        self._owner_view = view

    def refresh(self) -> None:
        view = self._owner_view
        achievements = view.achievements
        if not achievements:
            self.options = [
                discord.SelectOption(label="No achievements", value="0", default=True)
            ]
            self.disabled = True
            return
        self.disabled = False
        start = view.list_page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(achievements))
        options: list[discord.SelectOption] = []
        for i in range(start, end):
            ach = achievements[i]
            label = _truncate(f"{i + 1}. {ach.title or 'Untitled'}", SELECT_LABEL_TRUNCATE)
            desc = _truncate(ach.description or "", SELECT_DESC_TRUNCATE)
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(i),
                    description=desc or None,
                    default=(view.mode == MODE_DETAIL and view.detail_index == i),
                )
            )
        self.options = options

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._owner_view._on_drill(interaction, int(self.values[0]))


class AchievementsPagerView(OwnedView):
    """Three modes: game card → list (paged) → detail of one achievement."""

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

        self.mode: str = MODE_GAME
        self.list_page = 0
        self.detail_index = 0

        # ---- Row 0: navigation (contents change per mode) ----
        self._btn_game = discord.ui.Button(
            label="🎮 Game card", style=discord.ButtonStyle.secondary, row=0
        )
        self._btn_view_list = discord.ui.Button(
            label="📜 View achievements", style=discord.ButtonStyle.primary, row=0
        )
        self._btn_prev = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, row=0
        )
        self._btn_counter = discord.ui.Button(
            label="—", style=discord.ButtonStyle.secondary, row=0, disabled=True
        )
        self._btn_next = discord.ui.Button(
            label="Next ▶", style=discord.ButtonStyle.primary, row=0
        )
        self._btn_back_to_list = discord.ui.Button(
            label="⬅ Back to list", style=discord.ButtonStyle.secondary, row=0
        )

        self._btn_game.callback = self._on_game  # type: ignore[assignment]
        self._btn_view_list.callback = self._on_view_list  # type: ignore[assignment]
        self._btn_prev.callback = self._on_prev  # type: ignore[assignment]
        self._btn_next.callback = self._on_next  # type: ignore[assignment]
        self._btn_back_to_list.callback = self._on_back_to_list  # type: ignore[assignment]

        # ---- Row 1: drill-down select (list mode only) ----
        self._select = _DrillSelect(self)

        # ---- Row 2: external link ----
        self._btn_open_on_ra = discord.ui.Button(
            label="Open on RA",
            style=discord.ButtonStyle.link,
            url=game_url(game.game_id),
            row=2,
        )

        self._rebuild()

    # ---- state --------------------------------------------------------------

    @property
    def page_count(self) -> int:
        if not self.achievements:
            return 1
        return (len(self.achievements) + PAGE_SIZE - 1) // PAGE_SIZE

    def _rebuild(self) -> None:
        """Reset items to match the current mode. Buttons are re-added every
        time because Discord views don't support hiding individual items —
        clearing and re-adding is the cheapest correct option."""
        self.clear_items()

        if self.mode == MODE_GAME:
            if self.achievements:
                self.add_item(self._btn_view_list)
            self.add_item(self._btn_open_on_ra)
            return

        if self.mode == MODE_LIST:
            self.add_item(self._btn_game)
            self.add_item(self._btn_prev)
            self._btn_counter.label = f"Page {self.list_page + 1} / {self.page_count}"
            self.add_item(self._btn_counter)
            self.add_item(self._btn_next)
            self._btn_prev.disabled = self.list_page <= 0
            self._btn_next.disabled = self.list_page >= self.page_count - 1
            self._select.refresh()
            self.add_item(self._select)
            self.add_item(self._btn_open_on_ra)
            return

        # MODE_DETAIL
        self.add_item(self._btn_back_to_list)
        self.add_item(self._btn_prev)
        self._btn_counter.label = f"{self.detail_index + 1} / {len(self.achievements)}"
        self.add_item(self._btn_counter)
        self.add_item(self._btn_next)
        self._btn_prev.disabled = self.detail_index <= 0
        self._btn_next.disabled = self.detail_index >= len(self.achievements) - 1
        self.add_item(self._btn_open_on_ra)

    def current_embed(self) -> discord.Embed:
        if self.mode == MODE_GAME:
            if self.ra_username:
                return game_progress_embed(self.game, self.ra_username)
            return unlinked_game_embed(self.game)
        if self.mode == MODE_LIST:
            return _list_embed(
                self.game,
                self.achievements,
                self.list_page,
                self.page_count,
                self.ra_username,
            )
        return _detail_embed(
            self.game,
            self.achievements[self.detail_index],
            self.detail_index,
            len(self.achievements),
            self.ra_username,
        )

    async def _render(self, interaction: discord.Interaction) -> None:
        self._rebuild()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    # ---- callbacks ----------------------------------------------------------

    async def _on_game(self, interaction: discord.Interaction) -> None:
        self.mode = MODE_GAME
        await self._render(interaction)

    async def _on_view_list(self, interaction: discord.Interaction) -> None:
        self.mode = MODE_LIST
        self.list_page = 0
        await self._render(interaction)

    async def _on_back_to_list(self, interaction: discord.Interaction) -> None:
        self.mode = MODE_LIST
        # Land on the page containing the achievement we just left, so context
        # is preserved when bouncing in and out of detail.
        self.list_page = self.detail_index // PAGE_SIZE
        await self._render(interaction)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.mode == MODE_LIST:
            self.list_page = max(0, self.list_page - 1)
        elif self.mode == MODE_DETAIL:
            self.detail_index = max(0, self.detail_index - 1)
        await self._render(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.mode == MODE_LIST:
            self.list_page = min(self.page_count - 1, self.list_page + 1)
        elif self.mode == MODE_DETAIL:
            self.detail_index = min(
                len(self.achievements) - 1, self.detail_index + 1
            )
        await self._render(interaction)

    async def _on_drill(self, interaction: discord.Interaction, index: int) -> None:
        self.mode = MODE_DETAIL
        self.detail_index = max(0, min(index, len(self.achievements) - 1))
        await self._render(interaction)
