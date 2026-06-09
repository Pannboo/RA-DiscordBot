from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from sqlalchemy import delete, func, select

from bot.db import get_session
from bot.db.models import AwardSeen, GuildConfig, User
from bot.embeds.game import game_progress_embed, unlinked_game_embed
from bot.embeds.leaderboard import LeaderRow, leaderboard_embed
from bot.embeds.vs import VsSide, vs_embed
from bot.poller import Dispatcher
from bot.ra.client import RAClient, user_pic_url, user_url
from bot.ui import AdminHubView, HubView, build_admin_embed, build_hub_embed
from bot.ui.link_modal import LinkModal as _LinkModal

_GAME_ID_RE = re.compile(r"retroachievements\.org/game/(\d+)")


def _parse_game_id(text: str) -> int | None:
    s = text.strip()
    if s.isdigit():
        return int(s)
    m = _GAME_ID_RE.search(s)
    return int(m.group(1)) if m else None

log = logging.getLogger(__name__)


# Module-level group so all /ra commands share one tree node.
ra_group = app_commands.Group(name="ra", description="RetroAchievements commands")
admin_group = app_commands.Group(
    name="admin",
    description="Server-admin RetroAchievements commands",
    parent=ra_group,
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


def register(tree: app_commands.CommandTree, ra: RAClient, dispatcher: Dispatcher) -> None:
    """Attach all RA commands to the bot's command tree."""

    @ra_group.command(name="hub", description="Open the RetroAchievements control panel.")
    async def hub(interaction: discord.Interaction) -> None:
        view = await HubView.create(interaction.user.id, ra)
        embed = await build_hub_embed(interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @ra_group.command(name="link", description="Link your RetroAchievements account.")
    async def link(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_LinkModal(ra))

    @ra_group.command(name="unlink", description="Unlink your RetroAchievements account.")
    async def unlink(interaction: discord.Interaction) -> None:
        async with get_session() as session:
            user = await session.get(User, interaction.user.id)
            if user is None:
                await interaction.response.send_message(
                    "You don't have a linked RetroAchievements account.", ephemeral=True
                )
                return
            await session.execute(delete(User).where(User.discord_id == interaction.user.id))
            await session.commit()
        await interaction.response.send_message(
            "Unlinked. Your achievements will no longer be posted.", ephemeral=True
        )

    @ra_group.command(name="status", description="Show your current link status.")
    async def status(interaction: discord.Interaction) -> None:
        async with get_session() as session:
            user = await session.get(User, interaction.user.id)
        if user is None:
            await interaction.response.send_message(
                "No RA account linked. Use `/ra link` to set one up.", ephemeral=True
            )
            return
        last_poll = (
            discord.utils.format_dt(user.last_polled_at, "R") if user.last_polled_at else "never"
        )
        await interaction.response.send_message(
            f"Linked to **{user.ra_username}**.\n"
            f"Last poll: {last_poll}\n"
            f"Hardcore points: **{user.total_points:,}**  •  "
            f"Softcore: **{user.total_softcore_points:,}**  •  "
            f"RetroPoints: **{user.total_retro_points:,}**",
            ephemeral=True,
        )

    @ra_group.command(name="profile", description="Show a RetroAchievements profile.")
    @app_commands.describe(
        username="RA username to look up. Defaults to your linked account.",
    )
    async def profile(interaction: discord.Interaction, username: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        target = username
        if not target:
            async with get_session() as session:
                user = await session.get(User, interaction.user.id)
            if user is None:
                await interaction.followup.send(
                    "You haven't linked an account. Pass a username or run `/ra link` first.",
                    ephemeral=True,
                )
                return
            target = user.ra_username

        summary = await ra.get_user_summary(target, recent_games=1)
        if summary is None:
            await interaction.followup.send(
                f"Couldn't find a RetroAchievements user named **{target}**.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=summary.user,
            url=user_url(summary.user),
            description=summary.motto or "",
            color=0xCFB53B,
        )
        if summary.user_pic:
            embed.set_thumbnail(url=user_pic_url(summary.user_pic))
        embed.add_field(name="Hardcore", value=f"{summary.total_points:,} pts", inline=True)
        embed.add_field(
            name="Softcore", value=f"{summary.total_softcore_points:,} pts", inline=True
        )
        embed.add_field(
            name="RetroPoints", value=f"{summary.total_true_points:,}", inline=True
        )
        if summary.rank:
            embed.add_field(name="Rank", value=f"#{summary.rank:,}", inline=True)
        if summary.rich_presence_msg:
            embed.add_field(
                name="Currently", value=summary.rich_presence_msg, inline=False
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- Game lookup --------------------------------------------------------

    @ra_group.command(
        name="game",
        description="Show a game's set info and your progress.",
    )
    @app_commands.describe(game="RA game id or full retroachievements.org game URL")
    async def game_lookup(interaction: discord.Interaction, game: str) -> None:
        game_id = _parse_game_id(game)
        if game_id is None:
            await interaction.response.send_message(
                "Pass a numeric RA game id (e.g. `1`) or a full game URL "
                "(e.g. `https://retroachievements.org/game/1`).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # If the caller is linked, query against their RA account so progress lands.
        ra_username: str | None = None
        async with get_session() as session:
            user = await session.get(User, interaction.user.id)
            if user is not None:
                ra_username = user.ra_username

        lookup_user = ra_username or "RA-DiscordBot"  # any valid username works for set info
        info = await ra.get_game_info_and_user_progress(lookup_user, game_id)
        if info is None or info.title in (None, ""):
            await interaction.followup.send(
                f"Couldn't find a game with id `{game_id}`.", ephemeral=True
            )
            return

        if ra_username:
            embed = game_progress_embed(info, ra_username)
        else:
            embed = unlinked_game_embed(info)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- Leaderboard --------------------------------------------------------

    @ra_group.command(
        name="top",
        description="Top linked members in this server.",
    )
    @app_commands.describe(sort="Which stat to rank by")
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="Hardcore points", value="hardcore"),
            app_commands.Choice(name="Softcore points", value="softcore"),
            app_commands.Choice(name="RetroPoints", value="retropoints"),
            app_commands.Choice(name="Masteries", value="masteries"),
        ]
    )
    @app_commands.guild_only()
    async def top(
        interaction: discord.Interaction,
        sort: app_commands.Choice[str] | None = None,
    ) -> None:
        assert interaction.guild is not None
        sort_key = sort.value if sort else "hardcore"
        await interaction.response.defer(thinking=True)

        # Only members of this guild who are linked.
        guild_member_ids = {m.id for m in interaction.guild.members}

        async with get_session() as session:
            users = (await session.execute(select(User))).scalars().all()
            users = [u for u in users if u.discord_id in guild_member_ids]
            # Mastery counts per user — single grouped query.
            mastery_counts: dict[str, int] = {}
            if users:
                rows = await session.execute(
                    select(AwardSeen.ra_username, func.count())
                    .where(
                        AwardSeen.ra_username.in_([u.ra_username for u in users]),
                        AwardSeen.kind.in_(("mastered", "beaten_hc")),
                    )
                    .group_by(AwardSeen.ra_username)
                )
                mastery_counts = {ra_user: cnt for ra_user, cnt in rows.all()}

        rows = [
            LeaderRow(
                discord_id=u.discord_id,
                ra_username=u.ra_username,
                hardcore=u.total_points,
                softcore=u.total_softcore_points,
                retropoints=u.total_retro_points,
                masteries=mastery_counts.get(u.ra_username, 0),
            )
            for u in users
        ]
        rows.sort(
            key=lambda r: {
                "hardcore": r.hardcore,
                "softcore": r.softcore,
                "retropoints": r.retropoints,
                "masteries": r.masteries,
            }[sort_key],
            reverse=True,
        )
        rows = rows[:10]
        await interaction.followup.send(
            embed=leaderboard_embed(interaction.guild.name, rows, sort_key)
        )

    # ---- Head-to-head -------------------------------------------------------

    @ra_group.command(
        name="vs",
        description="Compare two linked users head-to-head.",
    )
    @app_commands.describe(
        member="The Discord user to compare against",
        other="Optional second user (defaults to you)",
    )
    @app_commands.guild_only()
    async def vs(
        interaction: discord.Interaction,
        member: discord.Member,
        other: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        left_id = (other or interaction.user).id
        right_id = member.id
        if left_id == right_id:
            await interaction.followup.send(
                "Pick two different users to compare.", ephemeral=True
            )
            return

        async with get_session() as session:
            left_user = await session.get(User, left_id)
            right_user = await session.get(User, right_id)

        missing = [
            (uid, u)
            for uid, u in ((left_id, left_user), (right_id, right_user))
            if u is None
        ]
        if missing:
            names = ", ".join(f"<@{uid}>" for uid, _ in missing)
            await interaction.followup.send(
                f"{names} hasn't linked a RetroAchievements account yet.", ephemeral=True
            )
            return

        # Pull fresh summaries for accurate side-by-side comparison.
        left_summary = await ra.get_user_summary(left_user.ra_username, recent_games=0)
        right_summary = await ra.get_user_summary(right_user.ra_username, recent_games=0)
        if left_summary is None or right_summary is None:
            await interaction.followup.send(
                "Couldn't reach RetroAchievements for one of the users.", ephemeral=True
            )
            return

        async with get_session() as session:
            mastery_rows = await session.execute(
                select(AwardSeen.ra_username, func.count())
                .where(
                    AwardSeen.ra_username.in_(
                        [left_user.ra_username, right_user.ra_username]
                    ),
                    AwardSeen.kind.in_(("mastered", "beaten_hc")),
                )
                .group_by(AwardSeen.ra_username)
            )
            masteries = {ra_user: cnt for ra_user, cnt in mastery_rows.all()}

        left_side = VsSide(
            ra_username=left_summary.user,
            pic=left_summary.user_pic,
            hardcore=left_summary.total_points,
            softcore=left_summary.total_softcore_points,
            retropoints=left_summary.total_true_points,
            rank=left_summary.rank,
            masteries=masteries.get(left_user.ra_username, 0),
        )
        right_side = VsSide(
            ra_username=right_summary.user,
            pic=right_summary.user_pic,
            hardcore=right_summary.total_points,
            softcore=right_summary.total_softcore_points,
            retropoints=right_summary.total_true_points,
            rank=right_summary.rank,
            masteries=masteries.get(right_user.ra_username, 0),
        )
        await interaction.followup.send(embed=vs_embed(left_side, right_side))

    # ---- Admin --------------------------------------------------------------

    @admin_group.command(
        name="hub",
        description="Open the server-admin RetroAchievements control panel.",
    )
    async def admin_hub(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        view = await AdminHubView.create(
            interaction.user.id, interaction.guild, dispatcher, ra
        )
        embed = await build_admin_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @admin_group.command(
        name="setchannel",
        description="Set this server's RetroAchievements announce channel.",
    )
    @app_commands.describe(channel="Channel where unlocks will be posted")
    async def setchannel(
        interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        assert interaction.guild_id is not None
        # Make sure the bot can actually post there.
        perms = channel.permissions_for(interaction.guild.me)  # type: ignore[union-attr]
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"I need **Send Messages** and **Embed Links** in {channel.mention}.",
                ephemeral=True,
            )
            return

        async with get_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild_id)
            if cfg is None:
                session.add(
                    GuildConfig(
                        guild_id=interaction.guild_id,
                        announce_channel_id=channel.id,
                        enabled=True,
                    )
                )
            else:
                cfg.announce_channel_id = channel.id
                cfg.enabled = True
            await session.commit()
        await dispatcher.refresh()
        await interaction.response.send_message(
            f"Announce channel set to {channel.mention}.", ephemeral=True
        )

    @admin_group.command(name="disable", description="Stop posting RA unlocks in this server.")
    async def disable(interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        async with get_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild_id)
            if cfg is None or not cfg.enabled:
                await interaction.response.send_message(
                    "Announcements are already disabled for this server.", ephemeral=True
                )
                return
            cfg.enabled = False
            await session.commit()
        await dispatcher.refresh()
        await interaction.response.send_message(
            "Disabled. Use `/ra admin setchannel` to re-enable.", ephemeral=True
        )

    @admin_group.command(name="status", description="Show this server's RA configuration.")
    async def admin_status(interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        async with get_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild_id)
        if cfg is None or cfg.announce_channel_id is None:
            await interaction.response.send_message(
                "No announce channel configured. Use `/ra admin setchannel`.", ephemeral=True
            )
            return
        state = "enabled" if cfg.enabled else "disabled"
        await interaction.response.send_message(
            f"Channel: <#{cfg.announce_channel_id}>  ·  status: **{state}**",
            ephemeral=True,
        )

    tree.add_command(ra_group)
