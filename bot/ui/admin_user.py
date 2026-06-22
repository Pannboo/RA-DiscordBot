from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from sqlalchemy import delete, select

from bot.db import get_session
from bot.db.models import User
from bot.poller.detector import seed_awards_on_link
from bot.ra.client import RAClient
from bot.ui.common import ConfirmView, OwnedView

log = logging.getLogger(__name__)


async def _load(discord_user_id: int) -> User | None:
    async with get_session() as session:
        return await session.get(User, discord_user_id)


async def _build_status_embed(
    target: discord.User | discord.Member, user: User | None
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Manage — {target.display_name}",
        color=0xCFB53B,
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    if user is None:
        embed.description = (
            f"{target.mention} is **not linked** to a RetroAchievements account.\n\n"
            "Use **Link account** to attach one on their behalf."
        )
    else:
        embed.description = (
            f"{target.mention} is linked to **{user.ra_username}**.\n\n"
            f"Hardcore: **{user.total_points:,} pts** · Softcore: **{user.total_softcore_points:,}**"
        )
    return embed


# -----------------------------------------------------------------------------
# Modal: admin enters RA username for a chosen Discord user.
# -----------------------------------------------------------------------------


class _AdminLinkModal(discord.ui.Modal, title="Link a user's RA account"):
    ra_username: discord.ui.TextInput = discord.ui.TextInput(
        label="RetroAchievements username",
        placeholder="e.g. Scott",
        min_length=2,
        max_length=32,
        required=True,
    )

    def __init__(
        self,
        ra: RAClient,
        target: discord.User | discord.Member,
        guild: discord.Guild,
        on_done,
    ) -> None:
        super().__init__()
        self._ra = ra
        self._target = target
        self._guild = guild
        self._on_done = on_done

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        username = str(self.ra_username.value).strip()
        summary = await self._ra.get_user_summary(username, recent_games=0)
        if summary is None:
            await interaction.followup.send(
                f"Couldn't find a RetroAchievements user named **{username}**.",
                ephemeral=True,
            )
            return

        async with get_session() as session:
            other = await session.scalar(
                select(User).where(
                    User.ra_username == summary.user,
                    User.discord_id != self._target.id,
                )
            )
            if other is not None:
                await interaction.followup.send(
                    f"**{summary.user}** is already linked to <@{other.discord_id}>.",
                    ephemeral=True,
                )
                return

            now = datetime.now(timezone.utc)
            existing = await session.get(User, self._target.id)
            if existing is None:
                session.add(
                    User(
                        discord_id=self._target.id,
                        ra_username=summary.user,
                        linked_at=now,
                        next_poll_at=now,
                        total_points=summary.total_points,
                        total_softcore_points=summary.total_softcore_points,
                        total_retro_points=summary.total_true_points,
                    )
                )
            else:
                existing.ra_username = summary.user
                existing.next_poll_at = now
                existing.total_points = summary.total_points
                existing.total_softcore_points = summary.total_softcore_points
                existing.total_retro_points = summary.total_true_points
            await session.commit()

        # Seed the award cache now so leaderboards are correct immediately.
        await seed_awards_on_link(self._ra, self._target.id)

        # Best-effort DM to the target so they know they were linked by an admin.
        admin = interaction.user
        try:
            dm_embed = discord.Embed(
                title="You've been linked to RetroAchievements",
                description=(
                    f"An administrator in **{self._guild.name}** linked your Discord account "
                    f"to the RetroAchievements user **{summary.user}**.\n\n"
                    "Your unlocks, awards, and milestones will be posted in that server's "
                    "configured RA channel from now on.\n\n"
                    "If this wasn't you, run `/ra unlink` in that server."
                ),
                color=0xCFB53B,
            )
            dm_embed.set_footer(text=f"Linked by {admin}")
            await self._target.send(embed=dm_embed)
            dm_status = "I also DMed them about it."
        except discord.Forbidden:
            dm_status = "I couldn't DM them (their DMs are closed)."
        except discord.DiscordException:
            log.exception("Failed to DM target user %s", self._target.id)
            dm_status = "I couldn't DM them."

        await interaction.followup.send(
            f"Linked {self._target.mention} to **{summary.user}**. {dm_status}",
            ephemeral=True,
        )
        await self._on_done()


# -----------------------------------------------------------------------------
# Status view: shown after a user is picked — Link / Unlink / Done buttons.
# -----------------------------------------------------------------------------


class AdminUserStatusView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        ra: RAClient,
        guild: discord.Guild,
        target: discord.User | discord.Member,
    ) -> None:
        super().__init__(owner_id, timeout=300)
        self._ra = ra
        self._guild = guild
        self._target = target

    @classmethod
    async def create(
        cls,
        owner_id: int,
        ra: RAClient,
        guild: discord.Guild,
        target: discord.User | discord.Member,
    ) -> "AdminUserStatusView":
        view = cls(owner_id, ra, guild, target)
        await view._sync_buttons()
        return view

    async def _sync_buttons(self) -> None:
        user = await _load(self._target.id)
        self.clear_items()

        link_btn = discord.ui.Button(
            label="Re-link account" if user else "Link account",
            style=discord.ButtonStyle.primary,
            emoji="🔗",
        )
        link_btn.callback = self._on_link  # type: ignore[assignment]
        self.add_item(link_btn)

        unlink_btn = discord.ui.Button(
            label="Unlink",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            disabled=user is None,
        )
        unlink_btn.callback = self._on_unlink  # type: ignore[assignment]
        self.add_item(unlink_btn)

    async def _repaint(self, interaction: discord.Interaction | None = None) -> None:
        await self._sync_buttons()
        user = await _load(self._target.id)
        embed = await _build_status_embed(self._target, user)
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            await self.message.edit(embed=embed, view=self)

    async def _on_link(self, interaction: discord.Interaction) -> None:
        modal = _AdminLinkModal(
            self._ra, self._target, self._guild, on_done=self._repaint
        )
        await interaction.response.send_modal(modal)

    async def _on_unlink(self, interaction: discord.Interaction) -> None:
        confirm = ConfirmView(self._owner_id, confirm_label="Unlink", danger=True)
        await interaction.response.send_message(
            f"Unlink {self._target.mention} from RetroAchievements?",
            view=confirm,
            ephemeral=True,
        )
        await confirm.wait()
        if not confirm.value:
            return
        async with get_session() as session:
            await session.execute(delete(User).where(User.discord_id == self._target.id))
            await session.commit()
        await self._repaint()


# -----------------------------------------------------------------------------
# Picker view: UserSelect → opens AdminUserStatusView for the chosen member.
# -----------------------------------------------------------------------------


class AdminUserPickerView(OwnedView):
    def __init__(self, owner_id: int, ra: RAClient, guild: discord.Guild) -> None:
        super().__init__(owner_id, timeout=300)
        self._ra = ra
        self._guild = guild

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Pick a Discord user to manage…",
        min_values=1,
        max_values=1,
    )
    async def user_select(
        self, interaction: discord.Interaction, select: discord.ui.UserSelect
    ) -> None:
        target = select.values[0]
        # UserSelect returns a User/Member union — prefer the guild member if available.
        member = self._guild.get_member(target.id) or target
        status_view = await AdminUserStatusView.create(
            self._owner_id, self._ra, self._guild, member
        )
        user = await _load(member.id)
        embed = await _build_status_embed(member, user)
        await interaction.response.send_message(
            embed=embed, view=status_view, ephemeral=True
        )
        status_view.message = await interaction.original_response()
