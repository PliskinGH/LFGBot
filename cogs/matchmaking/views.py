"""UI components for the matchmaking cog: LFGView, GameSettingsModal, ThreadRenameModal."""

from typing import Any, Callable, Coroutine

import discord

from . import constants
from .models import LFGContext


class LFGView(discord.ui.View):

    def __init__(self,
                 cog: 'Matchmaking' = None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label=constants.LFG_JOIN_BUTTON_LABEL,
                       emoji=constants.EMOJI_JOIN, style=discord.ButtonStyle.success,
                       custom_id=constants.LFG_JOIN_CUSTOM_ID)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_join(interaction, context)

    @discord.ui.button(label=constants.LFG_NOTIFY_BUTTON_LABEL,
                       emoji=constants.EMOJI_NOTIFY, style=discord.ButtonStyle.danger,
                       custom_id=constants.LFG_NOTIFY_CUSTOM_ID)
    async def notify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_notify(interaction, context)

    @discord.ui.button(label=constants.LFG_CANCEL_BUTTON_LABEL,
                       emoji=constants.EMOJI_CANCEL, style=discord.ButtonStyle.secondary,
                       custom_id=constants.LFG_CANCEL_CUSTOM_ID)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_cancel(interaction, context)

    @discord.ui.button(label=constants.LFG_START_BUTTON_LABEL,
                       emoji=constants.EMOJI_START, style=discord.ButtonStyle.primary,
                       custom_id=constants.LFG_START_CUSTOM_ID)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_start(interaction, context)


ModalCallback = Callable[
    [discord.Interaction, discord.ui.Modal, discord.ui.Select], Coroutine[Any, Any, None]
]
RenameModalCallback = Callable[
    [discord.Interaction, str], Coroutine[Any, Any, None]
]


class GameSettingsModal(discord.ui.Modal):

    description = discord.ui.TextInput(
        label="Description",
        placeholder="Provide details here...",
        style=discord.TextStyle.paragraph,
        max_length=200,
        required=False,
    )

    max_players = discord.ui.TextInput(
        label="Max number of players (2-100)",
        placeholder="Enter a whole number...",
        min_length=1,
        max_length=3,  # Prevents extremely large numbers
        required=False,
    )

    max_players_number = None  # This will hold the validated number of players

    def __init__(self, 
                 parent_select: discord.ui.Select | None = None,
                 title: str = "Game Settings",
                 on_confirm: ModalCallback | None = None,):
        super().__init__(title=title, timeout=300)
        self.parent_select = parent_select
        self.on_confirm = on_confirm

    async def on_submit(self, modal_interaction: discord.Interaction):
        # Validate that the input is a number
        if (self.max_players.value):
            try:
                value = int(self.max_players.value)
                if (value < 2 or value > 100):
                    raise ValueError
                self.max_players_number = value
            except ValueError:
                await modal_interaction.response.send_message(
                    "❌ **Invalid input:** Please enter a number from 2 to 100.",
                    ephemeral=True,
                )
                return

        if self.on_confirm:
            # Execute the custom callback passed during initialization
            await self.on_confirm(modal_interaction,
                                  self, self.parent_select)
        else:
            # Default fallback if no callback was provided
            await modal_interaction.response.send_message(
                f"Logged **{self.title}** request:\n> {self.description.value}",
                ephemeral=True,
            )


class ThreadRenameModal(discord.ui.Modal):

    title_input = discord.ui.TextInput(
        label="Thread title",
        placeholder="Enter a new thread title...",
        max_length=100,
        required=True,
    )

    def __init__(self, on_confirm: RenameModalCallback):
        super().__init__(title="Rename Thread", timeout=300)
        self.on_confirm = on_confirm

    async def on_submit(self, interaction: discord.Interaction):
        await self.on_confirm(interaction, self.title_input.value)
