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
    masteries: int  # awards_seen.kind = 'mastered'  (every achievement on HC)
    beatens: int  # awards_seen.kind = 'beaten_hc'  (win condition on HC)


SORT_LABELS = {
    "hardcore": "Hardcore Points",
    "softcore": "Softcore Points",
    "retropoints": "RetroPoints",
    "masteries": "Masteries",
    "beatens": "Beaten (not yet mastered)",
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
            "hardcore": f"{r.hardcore:,} HC pts",
            "softcore": f"{r.softcore:,} SC pts",
            "retropoints": f"{r.retropoints:,} RP",
            "masteries": f"{r.masteries:,} masteries",
            "beatens": f"{r.beatens:,} beaten",
        }[sort_key]
        # Always show a compact secondary line with the non-primary highlights.
        secondary_parts = []
        if sort_key != "hardcore":
            secondary_parts.append(f"🔥 {r.hardcore:,} HC")
        if sort_key != "masteries":
            secondary_parts.append(f"🏆 {r.masteries:,} Mastered")
        if sort_key != "beatens":
            secondary_parts.append(f"⚔️ {r.beatens:,} Beaten")
        secondary = " · ".join(secondary_parts)
        lines.append(
            f"{_rank_emoji(i)}  **[{r.ra_username}]({user_url(r.ra_username)})** — {primary}"
            + (f"\n　　*{secondary}*" if secondary else "")
        )
    embed.description = (embed.description or "") + "\n\n" + "\n".join(lines)
    embed.set_footer(text="RetroAchievements")
    return embed
