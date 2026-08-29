"""Help text for /lfg, per-game commands, and /rename."""

import discord

from common import utils

from . import constants
from .models import GuildGamesConfig


class HelpMixin:
    """Help text generation and the send_help entry point."""

    def _lfg_help_body(self, guild_config: GuildGamesConfig) -> str:
        # Help body shared between /lfg and the per-game alias commands.
        message = (
            "Create a looking-for-group post using either the guided "
            "menus or direct command arguments.\n"
            "## Guided mode\n"
            f"Use `/{constants.LFG_COMMAND}` without arguments to choose a game and enter "
            "the settings through the menus.\n"
            f"Providing only the `game` argument "
            f"(e.g. `/{constants.LFG_COMMAND} game:root`) opens the settings "
            "modal directly for that game.\n"
            "## Direct mode\n"
            f"`/{constants.LFG_COMMAND} game:<game> [description:<text>] "
            "[max_players:<number>]`\n"
            "### game\n"
            "The game/role to ping for this LFG post.\n"
            "### description\n"
            "Optional description for the game.\n"
            "### max_players\n"
            "Optional maximum number of players (including host) (2-100).\n"
            "The LFG will automatically close when this number is reached.\n"
            "Some games may have a default maximum number of players, which will be used if this argument is not provided.\n"
        )
        games = list(guild_config.games.values())
        if (games):
            alignment = len(max((game.command for game in games), key=len))
            game_lines = []
            for game in games:
                line = f"- `{game.command.ljust(alignment)}`"
                if (game.name):
                    line += (
                        " - "
                        f"{utils.indefinite_article(game.name)} **{game.name}** game"
                    )
                settings = []
                if (game.role):
                    settings.append(f"role to ping: {game.role}")
                if (game.forum):
                    forum = game.forum
                    if (forum.isdigit()):
                        forum = f"<#{forum}>"
                    settings.append(f"target forum: {forum}")
                if (game.default_max_guests is not None):
                    settings.append(f"players: {game.default_max_guests + 1}")
                if (settings):
                    line += " (" + ", ".join(settings) + ")"
                line += "."
                game_lines.append(line)
            message += "## Available games\n" + "\n".join(game_lines)
        else:
            message += "## Available games\nNo games are configured for this server."
        return message

    def _game_parameters_help_lines(self, game_command: str) -> str | None:
        """Render the game-parameters help section for a per-game command.

        Returns None when the game has no configured parameters. Parameters
        are direct-arguments-only (see _send_game_settings_modal), so the
        section says so explicitly.
        """
        accepted_params = self.game_parameters.get(game_command)
        if (not accepted_params):
            return None
        parameter_lines = [
            f"- `{param_name}`: {utils.format_accepted_values(values)}"
            for param_name, values in accepted_params.items()
        ]
        return (
            "## Game parameters\n"
            f"`/{game_command}` also accepts these arguments "
            "(comma-separate for several values). They are only available "
            "as command arguments, not in the guided menus:\n"
            + "\n".join(parameter_lines)
        )

    async def send_help(self, interaction: discord.Interaction, topic: str):
        guild_config = self.get_guild_config(interaction.guild_id)
        if (topic == constants.LFG_COMMAND):
            message = f"# Help: /{constants.LFG_COMMAND}\n" + self._lfg_help_body(guild_config)
            await interaction.response.send_message(message, ephemeral=True)
        elif (topic in guild_config.games):
            # Server-specific per-game command: an alias for /lfg game:<topic>,
            # so its help is the /lfg help preceded by an alias note, and —
            # when the game has configured parameters — a parameters section.
            message = (
                f"# Help: /{topic}\n"
                f"`/{topic}` is a shortcut for `/{constants.LFG_COMMAND} game:{topic}` — "
                f"the help for `/{constants.LFG_COMMAND}` below applies to it as well.\n\n"
                + self._lfg_help_body(guild_config)
            )
            parameters_section = self._game_parameters_help_lines(topic)
            if (parameters_section is not None):
                message += "\n" + parameters_section
            await interaction.response.send_message(message, ephemeral=True)
        elif (topic == constants.RENAME_COMMAND):
            message = (
                f"# Help: /{constants.RENAME_COMMAND}\n"
                "Rename a game thread created by the bot.\n"
                "Only the host can rename a thread, and only threads created by the bot can be renamed.\n"
                "## Guided mode\n"
                f"`/{constants.RENAME_COMMAND}` without arguments opens a modal to enter the new thread title.\n"
                "## Direct mode\n"
                f"`/{constants.RENAME_COMMAND} title:<new title>`\n"
                "### title\n"
                "The new title for the thread.\n"
            )
            await interaction.response.send_message(message, ephemeral=True)
        else:
            message = (
                f"# Help: /{topic}\n"
                "No detailed help is available for this command yet."
            )
            await interaction.response.send_message(message, ephemeral=True)
