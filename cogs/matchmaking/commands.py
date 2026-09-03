"""Slash commands for the matchmaking cog: /lfg and /rename."""

import discord
from discord import app_commands

from common import constants as common_constants, utils
from common.ui import DynamicSelectView

from . import constants
from .utils import fetch_host_id
from .views import ThreadRenameModal


class CommandsMixin:
    """The /lfg and /rename slash commands."""

    async def check_thread_rename_permission(self,
                                             interaction: discord.Interaction,
                                             use_followup: bool = False
                                             ) -> bool | None:
        """Whether the user may rename this thread; notifies on failure.

        Returns None when the channel is not a bot-created thread, False when
        the user is not the host, and True otherwise. The reason is sent as an
        ephemeral response, or as a followup when the interaction was already
        deferred (``use_followup``).
        """
        channel = interaction.channel

        async def _notify(message: str) -> None:
            if (use_followup):
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)

        if (channel is None or channel.type not in constants.THREAD_TYPES):
            await _notify(
                "This command can only be used inside a bot-created game thread."
            )
            return None

        if (channel.owner_id != self.bot.user.id):
            await _notify(
                "This thread cannot be renamed because it was not created by"
                " this bot.",
            )
            return False

        host_id = await fetch_host_id(channel)

        if (host_id is None):
            await _notify(
                "This thread cannot be renamed because the host could not be"
                " found."
            )
            return False

        if (host_id != interaction.user.id):
            await _notify("Only the host can rename this thread.")
            return False

        return True

    async def rename_thread(self, interaction: discord.Interaction,
                                 title: str):
        channel = interaction.channel
        new_name = utils.clean_thread_title(title)
        if (not new_name):
            await interaction.followup.send(
                "Thread title cannot be empty.", ephemeral=True
            )
            return

        await channel.edit(name=new_name)
        await interaction.followup.send(
            f"Thread renamed to **{new_name}**.", ephemeral=True
        )

    async def rename_thread_modal(self, interaction: discord.Interaction,
                                  title: str):
        # Defer ephemerally: the starter-message fetch and the channel edit
        # are REST calls that can outlast Discord's 3-second response window;
        # the followup webhook then stays usable for 15 minutes.
        await interaction.response.defer(ephemeral=True)

        if (not await self.check_thread_rename_permission(
                interaction, use_followup=True)):
            return

        await self.rename_thread(interaction, title)

    @app_commands.command(
        name=constants.RENAME_COMMAND, description=constants.RENAME_DESCRIPTION
    )
    @app_commands.describe(title="The new title for the thread.")
    async def rename(self, interaction: discord.Interaction,
                     title: str | None = None):
        guided = (title is None or not title.strip())
        if (not guided):
            # Direct mode: defer to avoid 3s timeout.
            # Guided mode cannot defer: the modal
            # must be the initial response.
            await interaction.response.defer(ephemeral=True)

        can_rename = await self.check_thread_rename_permission(
            interaction, use_followup=not guided)
        if (not can_rename):
            # Already notified (response or followup, per the mode).
            return

        if (guided):
            await interaction.response.send_modal(
                ThreadRenameModal(self.rename_thread_modal)
            )
            return

        await self.rename_thread(interaction, title)

    async def game_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild_config = self.get_guild_config(interaction.guild_id)

        return [
            app_commands.Choice(name=game.name, value=game.command)
            for game in guild_config.games.values()
            if current.lower() in game.command.lower()
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    @app_commands.command(
        name=constants.LFG_COMMAND, description=constants.LFG_DESCRIPTION
    )
    @app_commands.describe(
        game="The game/role to ping for this LFG post.",
        description="Optional description for the game.",
        max_players="Optional maximum number of players (including host) (2-100).",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def lfg(self, interaction: discord.Interaction,
                  game: str | None = None,
                  description: str | None = None,
                  max_players: app_commands.Range[int, 2, 100] | None = None,
                  ):
        if (not await self._guard_lfg_channel(interaction, constants.LFG_COMMAND)):
            return

        if (game is not None):
            if (description is None and max_players is None):
                # Modal route: a game without settings, same guided modal as
                # the per-game slash commands.
                await self._send_game_settings_modal(interaction, game)
                return

            # Direct mode: same creation path as the per-game slash commands.
            await self._direct_lfg(interaction, game, description, max_players)
            return

        if (description is not None or max_players is not None):
            await interaction.response.send_message(
                "The `game` argument is required when using direct settings.",
                ephemeral=True,
            )
            return

        choices = [ (game_option.name, game_option.command)
                    for game_option in self.get_guild_config(interaction.guild_id).games.values() ]

        view = DynamicSelectView(
            choices=choices, 
            command_interaction=interaction,
            on_select=self.process_game_selection,
            placeholder="Select a game option...",
            timeout=300
        )

        await interaction.response.send_message(
            view=view, ephemeral=True
        )
