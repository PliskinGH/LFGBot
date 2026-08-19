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
                       placeholder: str = "Choose an option..."):
        super().__init__(timeout=60)
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