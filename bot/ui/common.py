from __future__ import annotations

import discord


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
