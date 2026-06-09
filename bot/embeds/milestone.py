from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.ra.client import user_url

# Distinct color per milestone kind so the eye picks them up at a glance.
KIND_STYLES: dict[str, tuple[str, int, str]] = {
    "points_hc": ("Hardcore Points", 0xF59E0B, "🔥"),
    "points_sc": ("Softcore Points", 0x60A5FA, "💧"),
    "retropoints": ("RetroPoints", 0xEC4899, "💎"),
}


def points_milestone_embed(
    ra_username: str,
    kind: str,
    threshold: int,
    total: int,
) -> discord.Embed:
    label, color, icon = KIND_STYLES.get(kind, ("Points", 0xE74C3C, "🎯"))
    embed = discord.Embed(
        title=f"{icon}  {threshold:,} {label}",
        url=user_url(ra_username),
        description=(
            f"**{ra_username}** just crossed **{threshold:,}** {label}.\n"
            f"Lifetime total: **{total:,}**"
        ),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name="Milestone Reached", url=user_url(ra_username))
    embed.set_footer(text=f"RetroAchievements • {ra_username}")
    return embed
