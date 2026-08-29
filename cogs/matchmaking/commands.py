"""Slash commands for the matchmaking cog: /lfg and /rename."""

import discord
from discord import app_commands

from common import common, utils
from common.ui import DynamicSelectView

from . import constants
from .views import ThreadRenameModal


class CommandsMixin:
    """The /lfg and /rename slash commands."""

    async def check_thread_rename_permission(self,
                                             interaction: discord.Interaction
                                             ) -> bool | None:
        channel = interaction.channel
        if (channel is None or channel.type not in constants.THREAD_TYPES):
            return None

        if (channel.owner_id != self.bot.user.id):
            await interaction.response.send_message(
                "This thread cannot be renamed because it was not created by this bot.",
                ephemeral=True,
            )
            return False

        try:
            starter_message = await channel.fetch_message(channel.id)
        except discord.HTTPException:
            starter_message = None

        host_id = None
        if (starter_message is not None and starter_message.embeds):
            for field in starter_message.embeds[0].fields:
                if (field.name == "Host"):
                    host_id = utils.get_id_from_mention(field.value)
                    break

        if (host_id != interaction.user.id):
            await interaction.response.send_message(
                "Only the host can rename this thread.", ephemeral=True
            )
            return False

        return True

    async def rename_thread(self, interaction: discord.Interaction,
                                 title: str):
        channel = interaction.channel
        new_name = utils.clean_thread_title(title, self.custom_emoji_re)
        if (not new_name):
            await interaction.response.send_message(
                "Thread title cannot be empty.", ephemeral=True
            )
            return

        await channel.edit(name=new_name)
        await interaction.response.send_message(
            f"Thread renamed to **{new_name}**.", ephemeral=True
        )

    async def rename_thread_modal(self, interaction: discord.Interaction,
                                  title: str):
        if (not await self.check_thread_rename_permission(interaction)):
            return

        await self.rename_thread(interaction, title)

    @app_commands.command(
        name=constants.RENAME_COMMAND, description=constants.RENAME_DESCRIPTION
    )
    @app_commands.describe(title="The new title for the thread.")
    async def rename(self, interaction: discord.Interaction,
                     title: str | None = None):
        can_rename = await self.check_thread_rename_permission(interaction)
        if (can_rename is None):
            await interaction.response.send_message(
                "This command can only be used inside a bot-created game thread.",
                ephemeral=True,
            )
            return
        if (not can_rename):
            return

        if (title is None or not title.strip()):
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
        ][:common.AUTOCOMPLETE_LIMIT]

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
