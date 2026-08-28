import discord
from discord import app_commands
from discord.ext import commands

from common import common
from cogs.matchmaking import LFG_COMMAND, RENAME_COMMAND, Matchmaking
from cogs.matchrolls import MatchRolls, RANDOM_COMMAND

HELP_COGS = {
    LFG_COMMAND: Matchmaking.__name__,
    RENAME_COMMAND: Matchmaking.__name__,
    RANDOM_COMMAND: MatchRolls.__name__,
}


class Help(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def topic_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        messages = list(self.bot.tree.get_commands())
        if (interaction.guild is not None):
            # Include the guild-specific (per-game) slash commands.
            messages.extend(self.bot.tree.get_commands(guild=interaction.guild))

        available_topics = {
            command.name
            for command in messages
            if command.name != common.HELP_COMMAND
        }

        return [
            app_commands.Choice(name=topic, value=topic)
            for topic in sorted(available_topics)
            if current.lower() in topic.lower()
        ][:common.AUTOCOMPLETE_LIMIT]

    @app_commands.command(
        name=common.HELP_COMMAND,
        description="Get help with an LFG Bot command.",
    )
    @app_commands.describe(topic="The command to get help with.")
    @app_commands.autocomplete(topic=topic_autocomplete)
    async def help(self, interaction: discord.Interaction, topic: str):
        command = self.bot.tree.get_command(topic)
        if (command is None and interaction.guild is not None):
            command = self.bot.tree.get_command(topic, guild=interaction.guild)
        if command is None:
            await interaction.response.send_message(
                f"`/{topic}` is not currently available.",
                ephemeral=True,
            )
            return

        # Per-game guild commands are not in the static map; the cog that
        # registered the command records itself on the command's ``extras``.
        help_cog = self.bot.get_cog(HELP_COGS.get(topic, ""))
        if (help_cog is None):
            help_cog = getattr(command, "extras", {}).get("help_cog")
        if help_cog is None:
            await interaction.response.send_message(
                "Help is currently unavailable.", ephemeral=True
            )
            return

        await help_cog.send_help(interaction, topic)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))