from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from bot.ra.client import achievement_url, badge_url, game_icon_url, game_url
from bot.ra.consoles import format_console

AOTW_COLOR = 0xCFB53B  # RA gold


def _parse_start_at(start_at_str: str | None) -> datetime | None:
    if not start_at_str:
        return None
    try:
        return datetime.fromisoformat(start_at_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def aotw_embed(payload: dict) -> discord.Embed:
    """Build the Achievement-of-the-Week embed from the raw API payload."""
    achievement = payload.get("Achievement") or {}
    game = payload.get("Game") or {}
    console = payload.get("ConsoleName") or game.get("ConsoleName") or ""

    title = (achievement.get("Title") or "Achievement of the Week").strip()
    description = (achievement.get("Description") or "").strip()
    points = achievement.get("Points") or 0
    badge = achievement.get("BadgeName") or ""
    ach_id = achievement.get("ID")
    author = (achievement.get("Author") or "").strip()

    game_title = (game.get("Title") or "").strip()
    game_id = game.get("ID")
    game_icon = game.get("ImageIcon") or ""

    embed = discord.Embed(
        title=f"🏅  {title}",
        url=achievement_url(ach_id) if ach_id else None,
        description=f"> *{description or 'No description.'}*",
        color=AOTW_COLOR,
    )
    embed.set_author(name="Achievement of the Week")
    if badge:
        embed.set_thumbnail(url=badge_url(badge))

    if points:
        embed.add_field(name="💎  Points", value=f"**{points}** pts", inline=True)
    if game_title:
        embed.add_field(
            name="🎮  Game",
            value=f"[{game_title}]({game_url(game_id)})" if game_id else game_title,
            inline=True,
        )
    if console:
        embed.add_field(name="🕹️  Console", value=format_console(console), inline=True)

    start_at = _parse_start_at(payload.get("StartAt"))
    if start_at is not None:
        ends_at = start_at + timedelta(days=7)
        embed.add_field(
            name="⏳  Ends",
            value=(
                f"{discord.utils.format_dt(ends_at, 'F')} "
                f"({discord.utils.format_dt(ends_at, 'R')})"
            ),
            inline=False,
        )

    if author:
        embed.add_field(name="✍️  Set by", value=author, inline=True)

    if game_icon:
        embed.set_footer(
            text="RetroAchievements • Achievement of the Week",
            icon_url=game_icon_url(game_icon),
        )
    else:
        embed.set_footer(text="RetroAchievements • Achievement of the Week")

    # Use the rollover instant as the embed timestamp so Discord shows when it started.
    if start_at is not None:
        embed.timestamp = start_at
    else:
        embed.timestamp = datetime.now(timezone.utc)
    return embed
