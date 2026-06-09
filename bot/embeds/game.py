from __future__ import annotations

import discord

from bot.ra.client import game_icon_url, game_url
from bot.ra.consoles import format_console
from bot.ra.types import GameInfoProgress

GAME_COLOR = 0x2563EB  # blue
BAR_FILLED = "█"
BAR_EMPTY = "░"
BAR_WIDTH = 18


def _progress_bar(done: int, total: int) -> str:
    if total <= 0:
        return f"{BAR_EMPTY * BAR_WIDTH}  0%"
    pct = done / total
    filled = min(BAR_WIDTH, max(0, round(pct * BAR_WIDTH)))
    return f"{BAR_FILLED * filled}{BAR_EMPTY * (BAR_WIDTH - filled)}  {int(round(pct * 100))}%"


def game_progress_embed(
    game: GameInfoProgress, ra_username: str | None
) -> discord.Embed:
    total = game.num_achievements
    sc_done = game.num_awarded_to_user
    hc_done = game.num_awarded_to_user_hardcore

    embed = discord.Embed(
        title=game.title,
        url=game_url(game.game_id),
        color=GAME_COLOR,
    )
    if game.image_icon:
        embed.set_thumbnail(url=game_icon_url(game.image_icon))

    if game.console_name:
        embed.add_field(name="Console", value=format_console(game.console_name), inline=True)
    embed.add_field(name="Achievements", value=f"**{total}** in set", inline=True)

    if ra_username and total > 0:
        # Two bars: HC and SC. SC is "all awarded" (includes HC unlocks).
        embed.add_field(
            name=f"{ra_username}'s progress",
            value=(
                f"🔥 **Hardcore**\n`{_progress_bar(hc_done, total)}`  ({hc_done}/{total})\n"
                f"💧 **Softcore**\n`{_progress_bar(sc_done, total)}`  ({sc_done}/{total})"
            ),
            inline=False,
        )
    elif ra_username:
        embed.add_field(
            name=f"{ra_username}'s progress",
            value="*This set has no achievements yet.*",
            inline=False,
        )

    embed.set_footer(text="RetroAchievements")
    return embed


def unlinked_game_embed(game: GameInfoProgress) -> discord.Embed:
    """Shown when the caller isn't linked — same card without progress."""
    embed = discord.Embed(
        title=game.title,
        url=game_url(game.game_id),
        description="Link your account with `/ra link` to see your progress here.",
        color=GAME_COLOR,
    )
    if game.image_icon:
        embed.set_thumbnail(url=game_icon_url(game.image_icon))
    if game.console_name:
        embed.add_field(name="Console", value=format_console(game.console_name), inline=True)
    embed.add_field(
        name="Achievements", value=f"**{game.num_achievements}** in set", inline=True
    )
    embed.set_footer(text="RetroAchievements")
    return embed
