from __future__ import annotations

from dataclasses import dataclass

import discord

from bot.ra.client import user_url

LEADERBOARD_COLOR = 0xCFB53B


@dataclass
class LeaderRow:
    discord_id: int
    ra_username: str
    hardcore: int
    softcore: int
    retropoints: int
    masteries: int  # mastered + beaten_hc count from awards_seen


SORT_LABELS = {
    "hardcore": "Hardcore Points",
    "softcore": "Softcore Points",
    "retropoints": "RetroPoints",
    "masteries": "Masteries",
}


def _rank_emoji(i: int) -> str:
    return {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"`#{i + 1}`")


def leaderboard_embed(
    guild_name: str, rows: list[LeaderRow], sort_key: str
) -> discord.Embed:
    label = SORT_LABELS.get(sort_key, sort_key.title())
    embed = discord.Embed(
        title=f"🏆  {guild_name} Leaderboard",
        description=f"Top linked members by **{label}**",
        color=LEADERBOARD_COLOR,
    )
    if not rows:
        embed.description = "No linked members in this server yet. Use `/ra link` to join."
        return embed

    lines = []
    for i, r in enumerate(rows):
        primary = {
            "hardcore": f"{r.hardcore:,} HC",
            "softcore": f"{r.softcore:,} SC",
            "retropoints": f"{r.retropoints:,} RP",
            "masteries": f"{r.masteries:,} masteries",
        }[sort_key]
        secondary = " · ".join(
            v
            for v in [
                f"{r.hardcore:,} HC" if sort_key != "hardcore" else None,
                f"{r.masteries:,} 🏆" if sort_key != "masteries" else None,
            ]
            if v is not None
        )
        lines.append(
            f"{_rank_emoji(i)}  **[{r.ra_username}]({user_url(r.ra_username)})** — {primary}"
            + (f"\n　　*{secondary}*" if secondary else "")
        )
    embed.description = (embed.description or "") + "\n\n" + "\n".join(lines)
    embed.set_footer(text="RetroAchievements")
    return embed
