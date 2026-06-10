from __future__ import annotations

from dataclasses import dataclass

import discord

from bot.ra.client import user_pic_url, user_url

VS_COLOR = 0x8B5CF6


@dataclass
class VsSide:
    ra_username: str
    pic: str | None
    hardcore: int
    softcore: int
    retropoints: int
    rank: int | None
    masteries: int  # awards_seen.kind = 'mastered'
    beatens: int  # awards_seen.kind = 'beaten_hc'


def _arrow(a: int, b: int) -> tuple[str, str]:
    """Return left/right markers showing who's higher for this stat."""
    if a > b:
        return ("**", "")
    if b > a:
        return ("", "**")
    return ("", "")


def vs_embed(left: VsSide, right: VsSide) -> discord.Embed:
    embed = discord.Embed(
        title=f"{left.ra_username}  ⚔️  {right.ra_username}",
        color=VS_COLOR,
    )

    def row(label: str, l_val: int, r_val: int, fmt=lambda v: f"{v:,}") -> str:
        lb, rb = _arrow(l_val, r_val)
        return (
            f"**{label}**\n"
            f"{lb}{fmt(l_val)}{lb}  vs  {rb}{fmt(r_val)}{rb}"
        )

    embed.description = "\n\n".join(
        [
            row("🔥 Hardcore Points", left.hardcore, right.hardcore),
            row("💧 Softcore Points", left.softcore, right.softcore),
            row("💎 RetroPoints", left.retropoints, right.retropoints),
            row("🏆 Masteries", left.masteries, right.masteries),
            row("⚔️ Beaten (not mastered)", left.beatens, right.beatens),
        ]
    )

    # Site rank: lower is better — invert the arrow logic.
    if left.rank is not None and right.rank is not None:
        rank_left, rank_right = ("**", "") if left.rank < right.rank else (
            ("", "**") if right.rank < left.rank else ("", "")
        )
        embed.description += (
            f"\n\n**🌐 Global Rank**\n"
            f"{rank_left}#{left.rank:,}{rank_left}  vs  {rank_right}#{right.rank:,}{rank_right}"
        )

    left_link = f"[{left.ra_username}]({user_url(left.ra_username)})"
    right_link = f"[{right.ra_username}]({user_url(right.ra_username)})"
    embed.set_footer(text=f"{left.ra_username}  vs  {right.ra_username}  •  RetroAchievements")
    if left.pic:
        embed.set_thumbnail(url=user_pic_url(left.pic))
    embed.add_field(name="​", value=f"{left_link}   ⚡   {right_link}", inline=False)
    return embed
