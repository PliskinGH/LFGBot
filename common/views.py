"""Generic Discord UI components shared by the cogs."""

from typing import Any, Callable, Coroutine, Optional, Sequence, Awaitable
import discord

# Type hint for an async function accepting (interaction, select)
SelectCallback = Callable[
    [discord.Interaction, discord.Interaction, discord.ui.Select], Coroutine[Any, Any, None]
]

class DynamicSelect(discord.ui.Select):

    def __init__(
        self,
        choices: list[tuple[str, str]],
        command_interaction: discord.Interaction,
        on_select: SelectCallback | None = None,
        placeholder: str = "Choose an option...",
    ):
        options = [
            discord.SelectOption(label=label, value=val)
            for label, val in choices
        ]

        super().__init__(placeholder=placeholder, options=options)
        self.on_select = on_select
        self.command_interaction = command_interaction

    async def callback(self, select_interaction: discord.Interaction):
        if self.on_select:
            # Execute the custom callback passed during initialization
            await self.on_select(select_interaction, self.command_interaction, self)
        else:
            # Default fallback if no callback was provided
            await select_interaction.response.send_message(
                f"Selected: **{self.values[0]}**", ephemeral=True
            )

class DynamicSelectView(discord.ui.View):

    def __init__(self, choices: list[tuple[str, str]], 
                       command_interaction: discord.Interaction, 
                       on_select: SelectCallback | None = None,
                       placeholder: str = "Choose an option...",
                       timeout: Optional[float] = 60.0):
        super().__init__(timeout=timeout)
        self.add_item(DynamicSelect(choices, command_interaction, on_select, placeholder))

class DynamicButtonView(discord.ui.View):

    def __init__(
        self,
        buttons: Sequence[
            tuple[
                str,
                Optional[str],
                Callable[[discord.Interaction, discord.ui.View], Awaitable[None]],
            ]
            | tuple[
                str,
                Optional[str],
                Callable[[discord.Interaction, discord.ui.View], Awaitable[None]],
                discord.ButtonStyle,
            ]
        ],
        timeout: Optional[float] = 60.0,
    ):
        super().__init__(timeout=timeout)

        for item in buttons:
            label = item[0]
            emoji = item[1]
            user_callback = item[2]
            # Allows optional 4th tuple element for discord.ButtonStyle
            style = item[3] if len(item) > 3 else discord.ButtonStyle.primary

            button = discord.ui.Button(
                label=label, emoji=emoji, style=style
            )

            # Wrapper closure to pass button.view to user_callback
            def make_callback(callback, view):
                async def wrapper(interaction: discord.Interaction):
                    await callback(interaction, view)

                return wrapper

            # Reassign the button callback to the passed function/method
            button.callback = make_callback(user_callback, self)
            self.add_item(button)


class ConfirmView(discord.ui.View):
    """A confirm/cancel prompt for destructive commands.

    ``on_confirm``/``on_cancel`` are async callables returning the message
    text to show after the choice. Only the requesting user's presses are
    honored, and the buttons stop working once one is pressed.
    """

    def __init__(self, owner_id: int, confirm_label: str,
                 on_confirm, on_cancel, *, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

        confirm = discord.ui.Button(
            label=confirm_label, style=discord.ButtonStyle.danger)
        confirm.callback = self._confirm_callback
        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel_callback
        self.add_item(confirm)
        self.add_item(cancel)

    async def _finish(self, interaction: discord.Interaction,
                      callback) -> None:
        if (interaction.user.id != self.owner_id):
            await interaction.response.send_message(
                "Only the requesting user can answer this prompt.",
                ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=await callback(), view=self)

    async def _confirm_callback(self, interaction: discord.Interaction):
        await self._finish(interaction, self._on_confirm)

    async def _cancel_callback(self, interaction: discord.Interaction):
        await self._finish(interaction, self._on_cancel)