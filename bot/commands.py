from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from sqlalchemy import delete, select

from bot.db import get_session
from bot.db.models import GuildConfig, User
from bot.embeds.aotw import aotw_embed
from bot.embeds.game import game_progress_embed, unlinked_game_embed
from bot.ra import games_cache
from bot.ra.consoles import all_console_names, resolve_console
from bot.ui.achievements_pager import AchievementsPagerView
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

    @tree.error
    async def on_app_command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the **Manage Server** permission to use that command."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        log.exception("Unhandled application-command error", exc_info=error)

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
        await interaction.response.defer(thinking=True)
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
        if summary.member_since:
            embed.add_field(
                name="Member since",
                value=(
                    f"{discord.utils.format_dt(summary.member_since, 'D')} "
                    f"({discord.utils.format_dt(summary.member_since, 'R')})"
                ),
                inline=True,
            )
        if summary.rich_presence_msg:
            embed.add_field(
                name="Currently", value=summary.rich_presence_msg, inline=False
            )
        await interaction.followup.send(embed=embed)

    # ---- Achievement of the Week -------------------------------------------

    @ra_group.command(
        name="aotw",
        description="Show the current RetroAchievements Achievement of the Week.",
    )
    async def aotw(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await ra.get_achievement_of_the_week()
        if not data or not (data.get("Achievement") or {}).get("Title"):
            await interaction.followup.send(
                "Couldn't fetch the Achievement of the Week right now. Try again in a bit.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=aotw_embed(data), ephemeral=True)

    # ---- Game lookup --------------------------------------------------------

    @ra_group.command(
        name="game",
        description="Show a game's set info and your progress.",
    )
    @app_commands.describe(
        game="RA game id, full game URL, or game name (e.g. 'Bully')",
        console="Console name — required when searching by name (e.g. 'PlayStation 2')",
    )
    async def game_lookup(
        interaction: discord.Interaction,
        game: str,
        console: str | None = None,
    ) -> None:
        game_id = _parse_game_id(game)

        # Reply visibility is per-guild: admins flip it via the admin hub.
        # DMs / no guild → always ephemeral.
        public = False
        if interaction.guild is not None:
            async with get_session() as session:
                gcfg = await session.get(GuildConfig, interaction.guild.id)
            public = bool(gcfg and gcfg.game_lookup_public)

        await interaction.response.defer(ephemeral=not public, thinking=True)

        # Name-based search: resolve via the per-console game-list cache.
        if game_id is None:
            if not console:
                await interaction.followup.send(
                    "Searching by name needs a console. Pass `console:` "
                    "(e.g. `console: PlayStation 2`), or use a numeric RA id / game URL.",
                    ephemeral=True,
                )
                return
            resolved = resolve_console(console)
            if resolved is None:
                await interaction.followup.send(
                    f"Couldn't find a console matching **{console}**. Try the autocomplete.",
                    ephemeral=True,
                )
                return
            console_name, console_id = resolved
            games = await games_cache.get_games(ra, console_id)
            matches = games_cache.search(games, game, limit=5)
            if not matches:
                await interaction.followup.send(
                    f"No games on **{console_name}** matched **{game}**.",
                    ephemeral=True,
                )
                return
            # Use the top-ranked match. If there are multiple, hint the others
            # so the user can disambiguate by passing the full title.
            game_id = matches[0].id
            disambiguation = ""
            if len(matches) > 1 and games_cache.normalize(
                matches[0].title
            ) != games_cache.normalize(game):
                others = ", ".join(f"`{m.title}`" for m in matches[1:4])
                disambiguation = (
                    f"\n_Other matches on {console_name}: {others}_"
                )

        else:
            disambiguation = ""

        # If the caller is linked, query against their RA account so progress lands.
        ra_username: str | None = None
        async with get_session() as session:
            user = await session.get(User, interaction.user.id)
            if user is not None:
                ra_username = user.ra_username

        # When the caller isn't linked we still need a valid RA user for the
        # set-info lookup. Use the bot's own API account (guaranteed to exist)
        # rather than a placeholder name that may not be a real account.
        lookup_user = ra_username or ra.username
        info = await ra.get_game_with_types(lookup_user, game_id)
        if info is None or info.title in (None, ""):
            await interaction.followup.send(
                f"Couldn't find a game with id `{game_id}`.", ephemeral=True
            )
            return

        if ra_username:
            embed = game_progress_embed(info, ra_username)
        else:
            embed = unlinked_game_embed(info)

        view: discord.ui.View | None = None
        if info.achievements:
            view = AchievementsPagerView(info, ra_username, owner_id=interaction.user.id)

        await interaction.followup.send(
            content=disambiguation or None,
            embed=embed,
            view=view,
            ephemeral=not public,
        )

    @game_lookup.autocomplete("console")
    async def _console_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        names = all_console_names()
        q = (current or "").strip().lower()
        if q:
            filtered = [n for n in names if q in n.lower()]
        else:
            filtered = names
        return [app_commands.Choice(name=n, value=n) for n in filtered[:25]]

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
            app_commands.Choice(name="Beaten (Hardcore)", value="beatens"),
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

        # Masteries / beatens come from the per-user award counts cached from RA's
        # GetUserAwards (seeded on link, reconciled by the awards poller). The
        # beaten count is the raw hardcore-beaten total — which includes mastered
        # games — to mirror how RA displays these stats on user profiles.
        rows = [
            LeaderRow(
                discord_id=u.discord_id,
                ra_username=u.ra_username,
                hardcore=u.total_points,
                softcore=u.total_softcore_points,
                retropoints=u.total_retro_points,
                masteries=u.mastery_count,
                beatens=u.beaten_hardcore_count,
            )
            for u in users
        ]
        rows.sort(
            key=lambda r: {
                "hardcore": r.hardcore,
                "softcore": r.softcore,
                "retropoints": r.retropoints,
                "masteries": r.masteries,
                "beatens": r.beatens,
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

        def _side(summary, user) -> VsSide:  # noqa: ANN001
            return VsSide(
                ra_username=summary.user,
                pic=summary.user_pic,
                hardcore=summary.total_points,
                softcore=summary.total_softcore_points,
                retropoints=summary.total_true_points,
                rank=summary.rank,
                # Cached from RA's GetUserAwards (seeded on link, reconciled by the
                # awards poller). Raw hardcore-beaten includes mastered games —
                # matches RA's profile display.
                masteries=user.mastery_count,
                beatens=user.beaten_hardcore_count,
            )

        left_side = _side(left_summary, left_user)
        right_side = _side(right_summary, right_user)
        await interaction.followup.send(embed=vs_embed(left_side, right_side))

    # ---- Admin --------------------------------------------------------------

    @admin_group.command(
        name="hub",
        description="Open the server-admin RetroAchievements control panel.",
    )
    # ``default_permissions`` only controls Discord's initial command visibility;
    # server admins can override it. Enforce the requirement when a command runs.
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
