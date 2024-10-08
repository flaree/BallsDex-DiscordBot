from typing import Optional

import discord
from discord.ui import Button, View


class ConfirmChoiceView(View):
    def __init__(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.User] = None,
    ):
        super().__init__(timeout=90)
        self.value = None
        self.interaction = interaction
        self.user = user or interaction.user
        self.interaction_response: discord.Interaction

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.interaction_response = interaction

        if interaction.user != self.user:
            await interaction.response.send_message(
                "You cannot interact with this view.", ephemeral=True
            )
            return False

        if self.value is not None:
            await interaction.response.send_message(
                "You've already made a choice.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True  # type: ignore
        try:
            await self.interaction.followup.edit_message("@original", view=self)  # type: ignore
        except discord.NotFound:
            pass

    @discord.ui.button(
        style=discord.ButtonStyle.success, emoji="\N{HEAVY CHECK MARK}\N{VARIATION SELECTOR-16}"
    )
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        for item in self.children:
            item.disabled = True  # type: ignore
        await interaction.response.edit_message(
            content=interaction.message.content + "\nConfirmed", view=self  # type: ignore
        )
        self.value = True
        self.stop()

    @discord.ui.button(
        style=discord.ButtonStyle.danger,
        emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
    )
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        for item in self.children:
            item.disabled = True  # type: ignore
        await interaction.response.edit_message(
            content=interaction.message.content + "\nCancelled", view=self  # type: ignore
        )
        self.value = False
        self.stop()
