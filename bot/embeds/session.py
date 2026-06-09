from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.ra.client import game_icon_url, game_url, user_url
from bot.ra.consoles import format_console

SESSION_COLOR = 0x57F287  # Discord green — feels alive, not muted


def session_started_embed(
    ra_username: str,
    game_id: int,
    game_title: str,
    console_name: str,
    game_icon: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=game_title,
        url=game_url(game_id),
        description=f"**{ra_username}** booted up a new session — let's see how it goes.",
        color=SESSION_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name="▶  Now Playing", url=user_url(ra_username))
    if console_name:
        embed.add_field(name="Console", value=format_console(console_name), inline=True)
    if game_icon:
        embed.set_thumbnail(url=game_icon_url(game_icon))
    embed.set_footer(text=f"RetroAchievements • {ra_username}")
    return embed
