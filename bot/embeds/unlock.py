from __future__ import annotations

import discord

from bot.ra.client import achievement_url, badge_url, game_icon_url, game_url, user_url
from bot.ra.consoles import format_console
from bot.ra.types import RecentAchievement

HARDCORE_COLOR = 0xE5B83A  # warmer gold than the dusty CFB53B
SOFTCORE_COLOR = 0xBFC9D1  # cool silver


def unlock_embed(achievement: RecentAchievement, ra_username: str) -> discord.Embed:
    hc = achievement.is_hardcore
    color = HARDCORE_COLOR if hc else SOFTCORE_COLOR
    mode_emoji = "🔥" if hc else "💧"
    mode_label = "Hardcore" if hc else "Softcore"

    points_value = f"**+{achievement.points}** pts"
    if hc and achievement.true_ratio:
        points_value += f"\n💎 {achievement.true_ratio} RetroPoints"

    embed = discord.Embed(
        title=f"🏅  {achievement.title}",
        url=achievement_url(achievement.achievement_id),
        description=f"> *{achievement.description or 'No description.'}*",
        color=color,
        timestamp=achievement.date,
    )
    embed.set_author(name=f"{ra_username} unlocked an achievement", url=user_url(ra_username))
    if achievement.badge_name:
        embed.set_thumbnail(url=badge_url(achievement.badge_name))

    embed.add_field(name=f"{mode_emoji}  {mode_label}", value=points_value, inline=True)
    embed.add_field(
        name="🎮  Game",
        value=f"[{achievement.game_title}]({game_url(achievement.game_id)})",
        inline=True,
    )
    if achievement.console_name:
        embed.add_field(
            name="🕹️  Console", value=format_console(achievement.console_name), inline=True
        )

    if achievement.game_icon:
        embed.set_footer(text="RetroAchievements", icon_url=game_icon_url(achievement.game_icon))
    else:
        embed.set_footer(text="RetroAchievements")
    return embed
