from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.ra.client import game_icon_url, game_url, user_url
from bot.ra.consoles import format_console

SESSION_COLOR = 0x57F287  # Discord green — feels alive, not muted


def _format_gap(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, rem = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if rem == 0 else f"{hours}h {rem}m"
    days, rem_h = divmod(hours, 24)
    return f"{days}d" if rem_h == 0 else f"{days}d {rem_h}h"


def session_started_embed(
    ra_username: str,
    game_id: int,
    game_title: str,
    console_name: str,
    game_icon: str,
    *,
    replay_gap_minutes: int | None = None,
) -> discord.Embed:
    if replay_gap_minutes is not None:
        gap_str = _format_gap(replay_gap_minutes)
        description = (
            f"**{ra_username}** is back on this one after **{gap_str}** away."
        )
        author = "↩  Picking it back up"
    else:
        description = f"**{ra_username}** booted up a new session — let's see how it goes."
        author = "▶  Now Playing"
    embed = discord.Embed(
        title=game_title,
        url=game_url(game_id),
        description=description,
        color=SESSION_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=author, url=user_url(ra_username))
    if console_name:
        embed.add_field(name="Console", value=format_console(console_name), inline=True)
    if game_icon:
        embed.set_thumbnail(url=game_icon_url(game_icon))
    embed.set_footer(text=f"RetroAchievements • {ra_username}")
    return embed
