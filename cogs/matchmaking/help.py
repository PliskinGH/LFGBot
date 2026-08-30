"""Help text for /lfg, per-game commands, and /rename."""

import discord

from common import constants as common_constants, utils

from . import constants
from .models import GuildGamesConfig


class HelpMixin:
    """Help text generation and the send_help entry point."""

    def _lfg_help_intro(self) -> str:
        """The fixed /lfg usage instructions, shown as message content.

        This part does not depend on the server's games, so it stays short
        even when a guild configures many games (the game list goes in an
        embed, whose description allows more characters than the content).
        """
        return (
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
            # Discord strips trailing newlines from message content, so a
            # zero-width space keeps the two blank lines that separate the
            # text from the embeds below.
            "\u200b"
        )

    def _lfg_help_games(self, guild_config: GuildGamesConfig) -> str:
        """The per-server list of available games, shown in an embed.

        This part grows with the number of configured games, so it lives in
        the embed (whose description allows up to 4096 characters) rather
        than the message content (capped at 2000). The embed's title is
        "Available games", so the description is only the game lines.
        """
        games = list(guild_config.games.values())
        if (not games):
            return "No games are configured for this server."
        alignment = len(max((game.command for game in games), key=len))
        game_lines = []
        for game in games:
            line = f"- `{game.command.ljust(alignment)}`"
            if (game.name):
                line += (
                    " - "
                    f"{utils.indefinite_article(game.name)} **{game.name}** game"
                )
            settings = game.settings_summary()
            if (settings):
                line += " (" + ", ".join(settings) + ")"
            line += "."
            game_lines.append(line)
        return "\n".join(game_lines)

    @staticmethod
    def _embed(title: str, description: str) -> discord.Embed:
        """Build an embed, truncating its description to Discord's limit."""
        if (len(description) > common_constants.EMBED_DESCRIPTION_LIMIT):
            description = description[:common_constants.EMBED_DESCRIPTION_LIMIT - 3] + "..."
        return discord.Embed(title=title, description=description)

    def _games_embed(self, guild_config: GuildGamesConfig) -> discord.Embed:
        return self._embed("Available games", self._lfg_help_games(guild_config))

    def _params_embed(self, game_command: str) -> discord.Embed | None:
        section = self._game_parameters_help_lines(game_command)
        if (section is None):
            return None
        return self._embed("Game parameters", section)

    def _game_parameters_help_lines(self, game_command: str) -> str | None:
        """Render the game-parameters section for a per-game command.

        Returns None when the game has no configured parameters, otherwise
        the embed description listing each parameter with its display names
        only (the raw values are internal codes). Parameters are
        direct-arguments-only (see _send_game_settings_modal).
        """
        accepted_params = self.game_parameters.get(game_command)
        if (not accepted_params):
            return None
        parameter_lines = [
            f"- `{param_name}`: {', '.join(values.values())}"
            for param_name, values in accepted_params.items()
        ]
        return (
            f"`/{game_command}` also accepts these arguments "
            "(comma-separate for several values). They are only available "
            "as command arguments, not in the guided menus:\n"
            + "\n".join(parameter_lines)
        )

    async def send_help(self, interaction: discord.Interaction, topic: str):
        guild_config = self.get_guild_config(interaction.guild_id)
        embeds = []
        if (topic == constants.LFG_COMMAND):
            content = f"# Help: /{constants.LFG_COMMAND}\n\n" + self._lfg_help_intro()
            embeds.append(self._games_embed(guild_config))
        elif (topic in guild_config.games):
            # Server-specific per-game command: an alias for /lfg game:<topic>,
            # so its help is the /lfg usage preceded by an alias note; the
            # server's games and the game's parameters go in embeds.
            content = (
                f"# Help: /{topic}\n\n"
                f"`/{topic}` is a shortcut for `/{constants.LFG_COMMAND} game:{topic}` — "
                f"the help for `/{constants.LFG_COMMAND}` below applies to it as well.\n\n"
                + self._lfg_help_intro()
            )
            embeds.append(self._games_embed(guild_config))
            params_embed = self._params_embed(topic)
            if (params_embed is not None):
                embeds.append(params_embed)
        elif (topic == constants.RENAME_COMMAND):
            content = (
                f"# Help: /{constants.RENAME_COMMAND}\n\n"
                "Rename a game thread created by the bot.\n"
                "Only the host can rename a thread, and only threads created by the bot can be renamed.\n"
                "## Guided mode\n"
                f"`/{constants.RENAME_COMMAND}` without arguments opens a modal to enter the new thread title.\n"
                "## Direct mode\n"
                f"`/{constants.RENAME_COMMAND} title:<new title>`\n"
                "### title\n"
                "The new title for the thread.\n"
            )
        else:
            content = f"# Help: /{topic}\n\nNo detailed help is available for this command yet."

        
        if (len(content) > common_constants.MESSAGE_CONTENT_LIMIT):
            # Safety net: plain content is capped at 2000 characters. The
            # variable parts (games list, parameters) live in embeds, so the
            # content is fixed length and truncating it would only cut the tail.
            content = content[:common_constants.MESSAGE_CONTENT_LIMIT - 3] + "..."
        await interaction.response.send_message(
            content=content, embeds=embeds or None, ephemeral=True)
