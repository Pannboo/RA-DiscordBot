"""List-view achievement browser for `/ra game`.

The default view is a compact list of ~8 achievements with title, points and
your unlock state, so you can scan a whole set fast. Drill into any single
achievement via the "Open one…" dropdown to see its badge, full description
and award stats. A "Game card" button always brings you back to the set
overview. A "Filter" dropdown narrows the list to one achievement type.
Ephemeral and owned by the invoking user.
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

# Markers for the RA achievement `type` field. Kept distinct from the status
# emoji set (🔥 💧 🔒 🏅) so they can sit next to each other without confusion.
TYPE_EMOJI: dict[str, str] = {
    "progression": "🎯",
    "win_condition": "🏁",
    "missable": "⚠️",
}
TYPE_LABEL: dict[str, str] = {
    "progression": "Progression",
    "win_condition": "Win Condition",
    "missable": "Missable",
}
FILTER_ALL = "all"
FILTER_CHOICES: list[tuple[str, str, str]] = [
    # (value, label, emoji)
    (FILTER_ALL, "All achievements", "📜"),
    ("progression", "Progression only", "🎯"),
    ("win_condition", "Win Condition only", "🏁"),
    ("missable", "Missable only", "⚠️"),
]


def _status_emoji(ach: AchievementInfo, linked: bool) -> str:
    if not linked:
        return "🏅"
    if ach.date_earned_hardcore is not None:
        return "🔥"
    if ach.date_earned is not None:
        return "💧"
    return "🔒"


def _type_marker(ach: AchievementInfo) -> str:
    """Inline icon for the achievement type, or empty string when no type."""
    if not ach.type:
        return ""
    return TYPE_EMOJI.get(ach.type.lower(), "")


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _list_embed(
    game: GameInfoProgress,
    achievements: list[AchievementInfo],
    filtered: list[tuple[int, AchievementInfo]],
    page: int,
    page_count: int,
    ra_username: str | None,
    filter_value: str,
) -> discord.Embed:
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(filtered))
    linked = ra_username is not None

    lines: list[str] = []
    seen_types: set[str] = set()
    for orig_idx, ach in filtered[start:end]:
        status = _status_emoji(ach, linked)
        marker = _type_marker(ach)
        if marker and ach.type:
            seen_types.add(ach.type.lower())
        title = ach.title or "Untitled"
        marker_suffix = f" {marker}" if marker else ""
        head = (
            f"{status} **{orig_idx + 1}. [{title}]({achievement_url(ach.id)})**"
            f"{marker_suffix} — `{ach.points} pts`"
        )
        if ach.true_ratio:
            head += f" • 💎 `{ach.true_ratio}`"
        desc = _truncate(ach.description or "", LIST_DESC_TRUNCATE)
        lines.append(head + (f"\n> {desc}" if desc else ""))

    if filter_value == FILTER_ALL:
        title_suffix = ""
    else:
        title_suffix = f" — {TYPE_EMOJI.get(filter_value, '')} {TYPE_LABEL.get(filter_value, filter_value)}"

    description = (
        "\n\n".join(lines)
        if lines
        else f"*No {TYPE_LABEL.get(filter_value, filter_value).lower()} achievements in this set.*"
        if filter_value != FILTER_ALL
        else "*This set has no achievements yet.*"
    )

    embed = discord.Embed(
        title=f"{game.title} — Achievements{title_suffix}",
        url=game_url(game.game_id),
        description=description,
        color=ACHIEVEMENT_COLOR,
    )
    if game.image_icon:
        embed.set_thumbnail(url=game_icon_url(game.image_icon))

    summary_bits: list[str] = []
    if filter_value == FILTER_ALL:
        summary_bits.append(f"{len(achievements)} in set")
    else:
        summary_bits.append(f"{len(filtered)} of {len(achievements)} match filter")
    if game.console_name:
        summary_bits.append(format_console(game.console_name))
    if linked:
        hc = sum(1 for _, a in filtered if a.date_earned_hardcore is not None)
        sc = sum(
            1
            for _, a in filtered
            if a.date_earned is not None and a.date_earned_hardcore is None
        )
        summary_bits.append(f"🔥 {hc} HC • 💧 {sc} SC")
    embed.add_field(name="​", value=" • ".join(summary_bits), inline=False)

    legend_order = ("progression", "win_condition", "missable")
    legend_parts = [
        f"{TYPE_EMOJI[k]} {TYPE_LABEL[k]}" for k in legend_order if k in seen_types
    ]
    if legend_parts:
        embed.add_field(name="Key", value=" • ".join(legend_parts), inline=False)

    embed.set_footer(text=f"Page {page + 1} of {page_count}")
    return embed


def _detail_embed(
    game: GameInfoProgress,
    achievement: AchievementInfo,
    position: int,
    total_in_view: int,
    original_index: int,
    total_in_set: int,
    ra_username: str | None,
    filter_value: str,
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
        key = achievement.type.lower()
        pretty = TYPE_LABEL.get(key, achievement.type.replace("_", " ").title())
        icon = TYPE_EMOJI.get(key, "")
        embed.add_field(
            name="Type",
            value=f"{icon} {pretty}".strip(),
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

    if filter_value == FILTER_ALL:
        position_str = f"Achievement {original_index + 1} of {total_in_set}"
    else:
        label = TYPE_LABEL.get(filter_value, filter_value)
        position_str = (
            f"{label} {position + 1} of {total_in_view} "
            f"(#{original_index + 1} of {total_in_set} in set)"
        )
    footer = f"{game.title} • {game.console_name} • {position_str}"
    if achievement.author:
        footer += f" • set by {achievement.author}"
    embed.set_footer(
        text=footer,
        icon_url=game_icon_url(game.image_icon) if game.image_icon else None,
    )
    return embed


class _DrillSelect(discord.ui.Select["AchievementsPagerView"]):
    """Dropdown listing the achievements visible on the current list page."""

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
        filtered = view.filtered
        if not filtered:
            self.options = [
                discord.SelectOption(label="No achievements", value="0", default=True)
            ]
            self.disabled = True
            return
        self.disabled = False
        start = view.list_page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(filtered))
        options: list[discord.SelectOption] = []
        for filt_idx in range(start, end):
            orig_idx, ach = filtered[filt_idx]
            label = _truncate(
                f"{orig_idx + 1}. {ach.title or 'Untitled'}", SELECT_LABEL_TRUNCATE
            )
            desc = _truncate(ach.description or "", SELECT_DESC_TRUNCATE)
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(filt_idx),
                    description=desc or None,
                    default=(view.mode == MODE_DETAIL and view.detail_index == filt_idx),
                )
            )
        self.options = options

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._owner_view._on_drill(interaction, int(self.values[0]))


class _FilterSelect(discord.ui.Select["AchievementsPagerView"]):
    """Narrows the list to one achievement type."""

    def __init__(self, view: "AchievementsPagerView") -> None:
        super().__init__(
            placeholder="Filter…",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="placeholder", value=FILTER_ALL)],
            row=2,
        )
        self._owner_view = view

    def refresh(self) -> None:
        view = self._owner_view
        counts = view.type_counts
        options: list[discord.SelectOption] = []
        for value, label, emoji in FILTER_CHOICES:
            if value == FILTER_ALL:
                total = len(view.achievements)
                description = f"{total} achievement{'s' if total != 1 else ''}"
            else:
                n = counts.get(value, 0)
                description = f"{n} achievement{'s' if n != 1 else ''}"
            options.append(
                discord.SelectOption(
                    label=label,
                    value=value,
                    emoji=emoji,
                    description=description,
                    default=(view.filter_value == value),
                )
            )
        self.options = options

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._owner_view._on_filter(interaction, self.values[0])


class AchievementsPagerView(OwnedView):
    """Three modes: game card → list (paged, filterable) → detail."""

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
        # `detail_index` is an index into `self.filtered` (matches Prev/Next semantics).
        self.detail_index = 0
        self.filter_value: str = FILTER_ALL

        # ---- Row 0: navigation ----
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

        # ---- Row 1: drill-down select ----
        self._drill_select = _DrillSelect(self)

        # ---- Row 2: filter select ----
        self._filter_select = _FilterSelect(self)

        # ---- Row 3: external link ----
        self._btn_open_on_ra = discord.ui.Button(
            label="Open on RA",
            style=discord.ButtonStyle.link,
            url=game_url(game.game_id),
            row=3,
        )

        self._rebuild()

    # ---- state --------------------------------------------------------------

    @property
    def filtered(self) -> list[tuple[int, AchievementInfo]]:
        """Achievements matching the active filter, paired with their index in the full set."""
        if self.filter_value == FILTER_ALL:
            return list(enumerate(self.achievements))
        target = self.filter_value
        return [
            (i, a)
            for i, a in enumerate(self.achievements)
            if a.type and a.type.lower() == target
        ]

    @property
    def type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self.achievements:
            if a.type:
                counts[a.type.lower()] = counts.get(a.type.lower(), 0) + 1
        return counts

    @property
    def page_count(self) -> int:
        n = len(self.filtered)
        if n == 0:
            return 1
        return (n + PAGE_SIZE - 1) // PAGE_SIZE

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
            self._drill_select.refresh()
            self.add_item(self._drill_select)
            self._filter_select.refresh()
            self.add_item(self._filter_select)
            self.add_item(self._btn_open_on_ra)
            return

        # MODE_DETAIL
        filtered = self.filtered
        total = len(filtered)
        self.add_item(self._btn_back_to_list)
        self.add_item(self._btn_prev)
        self._btn_counter.label = (
            f"{self.detail_index + 1} / {total}" if total else "0 / 0"
        )
        self.add_item(self._btn_counter)
        self.add_item(self._btn_next)
        self._btn_prev.disabled = self.detail_index <= 0
        self._btn_next.disabled = self.detail_index >= total - 1
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
                self.filtered,
                self.list_page,
                self.page_count,
                self.ra_username,
                self.filter_value,
            )
        filtered = self.filtered
        if not filtered:
            return discord.Embed(
                title=self.game.title,
                description="Nothing to show with the current filter.",
                color=ACHIEVEMENT_COLOR,
            )
        orig_idx, ach = filtered[self.detail_index]
        return _detail_embed(
            self.game,
            ach,
            self.detail_index,
            len(filtered),
            orig_idx,
            len(self.achievements),
            self.ra_username,
            self.filter_value,
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
        # Land on the page containing the achievement we just left.
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
                max(0, len(self.filtered) - 1), self.detail_index + 1
            )
        await self._render(interaction)

    async def _on_drill(self, interaction: discord.Interaction, index: int) -> None:
        self.mode = MODE_DETAIL
        total = len(self.filtered)
        self.detail_index = max(0, min(index, total - 1)) if total else 0
        await self._render(interaction)

    async def _on_filter(self, interaction: discord.Interaction, value: str) -> None:
        """Changing filter snaps back to the list at page 0 — predictable,
        and avoids weirdness when the previously-viewed achievement isn't in
        the new filter."""
        self.filter_value = value
        self.list_page = 0
        self.detail_index = 0
        if self.mode != MODE_GAME:
            self.mode = MODE_LIST
        await self._render(interaction)
