from __future__ import annotations

import discord


async def has_manage_guild_permission(
    interaction: discord.Interaction, guild: discord.Guild
) -> bool:
    """Return whether the interaction user currently has Manage Server.

    Component interactions normally include a ``Member`` object, but fetch from
    the guild as a fallback so the check remains reliable if the member cache is
    incomplete.
    """
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None:
        member = guild.get_member(interaction.user.id)
    if member is None:
        try:
            member = await guild.fetch_member(interaction.user.id)
        except discord.DiscordException:
            return False
    return member.guild_permissions.manage_guild


class OwnedView(discord.ui.View):
    """A view that only responds to interactions from a specific user.

    Useful for ephemeral panels — without this, other users seeing the message
    in dev (or via screenshare) can't drive someone else's controls.
    """

    def __init__(self, owner_id: int, *, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self._owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message(
                "This panel isn't yours. Run the command yourself to open one.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[attr-defined]


class AdminView(OwnedView):
    """An owner-locked view that also enforces Manage Server on every click."""

    def __init__(
        self, owner_id: int, guild: discord.Guild, *, timeout: float = 300.0
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self._guild = guild

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if not await has_manage_guild_permission(interaction, self._guild):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to use this panel.",
                ephemeral=True,
            )
            return False
        return True


class ConfirmView(OwnedView):
    """Two-button confirm dialog. `value` is True / False / None (timeout)."""

    def __init__(self, owner_id: int, *, confirm_label: str = "Yes", danger: bool = False) -> None:
        super().__init__(owner_id, timeout=60)
        self.value: bool | None = None

        confirm = discord.ui.Button(
            label=confirm_label,
            style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary,
        )
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

        async def _confirm(interaction: discord.Interaction) -> None:
            self.value = True
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = True  # type: ignore[attr-defined]
            await interaction.response.edit_message(view=self)
            self.stop()

        async def _cancel(interaction: discord.Interaction) -> None:
            self.value = False
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = True  # type: ignore[attr-defined]
            await interaction.response.edit_message(view=self)
            self.stop()

        confirm.callback = _confirm  # type: ignore[assignment]
        cancel.callback = _cancel  # type: ignore[assignment]
        self.add_item(confirm)
        self.add_item(cancel)
