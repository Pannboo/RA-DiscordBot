from __future__ import annotations

import discord

from bot.ra.client import game_icon_url, game_url, user_url
from bot.ra.consoles import format_console
from bot.ra.types import AwardEntry

# (header label, embed color, banner/flavour copy)
AWARD_STYLES: dict[str, tuple[str, int, str]] = {
    "mastered": (
        "🏆  Mastered",
        0xC084FC,  # vivid purple
        "Every achievement, on hardcore. The whole set, the whole vibe.",
    ),
    "completed": (
        "✅  Completed",
        0xA0AEC0,  # slate grey
        "Every achievement unlocked. Cleaned out the set.",
    ),
    "beaten_hc": (
        "⚔️  Beaten — Hardcore",
        0x60A5FA,  # vivid blue
        "Cleared on hardcore. Roll credits.",
    ),
    "beaten_sc": (
        "🛡️  Beaten — Softcore",
        0x93C5FD,  # lighter blue
        "Cleared on softcore. Credits roll.",
    ),
}


def award_embed(ra_username: str, kind: str, award: AwardEntry) -> discord.Embed:
    header, color, flavour = AWARD_STYLES.get(
        kind, ("🎖️  Award", 0x9B59B6, "A new award was earned.")
    )
    game_title = award.title or "Unknown game"
    game_id = award.award_data or 0

    embed = discord.Embed(
        title=game_title,
        url=game_url(game_id) if game_id else None,
        description=f"> *{flavour}*",
        color=color,
        timestamp=award.awarded_at,
    )
    embed.set_author(name=f"{ra_username} — {header}", url=user_url(ra_username))
    if award.console_name:
        embed.add_field(name="Console", value=format_console(award.console_name), inline=True)
    embed.add_field(name="Award", value=award.award_type, inline=True)
    if award.image_icon:
        # Award is a milestone moment — show the cover art as a hero image, not just a thumbnail.
        embed.set_image(url=game_icon_url(award.image_icon))
    embed.set_footer(text=f"RetroAchievements • {ra_username}")
    return embed
