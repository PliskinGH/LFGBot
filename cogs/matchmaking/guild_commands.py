"""Guild-specific dynamic command registration and per-game parameters."""

import inspect
from typing import Optional

import discord
from discord import app_commands

from common import common

from . import constants


class GuildCommandsMixin:
    """Dynamic per-game slash command registration and their parameter handling."""

    def register_guild_commands(self) -> None:
        # Register one guild-specific slash command per configured game.
        # Games configured only in the DEFAULT section are not registered:
        # guilds not listed in the games config keep relying on /lfg.
        registered_guild_ids = getattr(self.bot, 'provided_guild_ids', None)
        for guild_id, guild_config in self.guilds.items():
            guild = discord.Object(id=guild_id)
            for game_command in guild_config.games:
                # No local name validation: Command.__init__ runs discord.py's
                # own validate_name, so an impossible name (e.g. "c&c" — a fine
                # /lfg value but not a legal slash-command name) surfaces here
                # as ValueError and is skipped instead of crashing the load.
                try:
                    command = self._make_game_command(game_command)
                except ValueError as error:
                    print(
                        f"Skipping guild-specific command '{game_command}' for "
                        f"guild {guild_id}: {str(error).rstrip('.')} — it remains "
                        f"usable through /{constants.LFG_COMMAND}."
                    )
                    continue
                self.bot.tree.add_command(command, guild=guild)
            if (registered_guild_ids is not None):
                registered_guild_ids.add(guild_id)

    def _make_game_command(self, game_command: str) -> app_commands.Command:
        # extras is never serialized by discord.py; "help_cog" lets the /help
        # command find this command's owning cog without hardcoding cog names.
        return app_commands.Command(
            name=game_command,
            description=constants.LFG_DESCRIPTION,
            callback=self._make_game_callback(game_command),
            extras={"help_cog": self},
        )

    def _make_game_callback(self, game_command: str):
        accepted_params = self.game_parameters.get(game_command, {})
        parameter_names = list(accepted_params.keys())

        async def game_callback(interaction, **command_kwargs):
            return await self._run_game_command(interaction, game_command, command_kwargs)

        signature_parameters = [
            inspect.Parameter(
                "interaction",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=discord.Interaction,
            ),
            inspect.Parameter(
                "description",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Optional[str],
                default=None,
            ),
            inspect.Parameter(
                "max_players",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Optional[int],
                default=None,
            ),
        ]
        for param_name in parameter_names:
            signature_parameters.append(
                inspect.Parameter(
                    param_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Optional[str],
                    default=None,
                )
            )

        descriptions = {
            "description": "Optional description for the game.",
            "max_players": "Optional maximum number of players (2-100).",
        }
        autocompletes = {}
        for param_name in parameter_names:
            descriptions[param_name] = f"Optional {param_name} values for this game."
            autocompletes[param_name] = self._make_param_autocomplete(
                game_command, param_name)

        game_callback.__qualname__ = game_callback.__name__
        game_callback.__signature__ = inspect.Signature(signature_parameters)
        game_callback.__discord_app_commands_param_description__ = descriptions
        game_callback.__discord_app_commands_param_autocomplete__ = autocompletes
        return game_callback

    def _make_param_autocomplete(self, game_command: str, param_name: str):
        accepted_values = self.game_parameters.get(game_command, {}).get(param_name, [])

        async def autocomplete(interaction, current):
            # Discord replaces the whole field with the picked choice's value,
            # so each choice's value is composed with the existing field prefix
            # (everything up to the last comma): picking a value appends it as
            # an additional comma-separated value instead of overwriting the
            # previously picked ones.
            #
            # The choice's display name is the composed string too: some Discord
            # clients commit the choice by writing its name rather than its
            # value, and using the bare value as the name would then discard
            # the prefix (overwriting earlier picks). With both equal, either
            # write path preserves the full composed value.
            prefix = current[: current.rfind(",") + 1]
            last_token = current[len(prefix):].strip().lower()
            already_present = {
                token.strip().lower() for token in current.split(",")[:-1]
            }

            choices = []
            for value in accepted_values:
                if (last_token and last_token not in value.lower()):
                    continue
                if (value.lower() in already_present):
                    continue
                composed = prefix + value
                if (len(composed) > 100):  # Choice string values cap at 100 chars.
                    continue
                choices.append(app_commands.Choice(name=composed, value=composed))
                if (len(choices) >= common.AUTOCOMPLETE_LIMIT):
                    break
            return choices

        autocomplete.__qualname__ = autocomplete.__name__
        return autocomplete

    @staticmethod
    def _parse_param_values(raw_value, accepted_values):
        if (raw_value is None):
            return None, None
        provided = [value.strip() for value in raw_value.split(",") if value.strip()]
        invalid = [value for value in provided if value not in accepted_values]
        if (invalid):
            return None, invalid
        return provided, None

    async def _run_game_command(self, interaction, game_command, command_kwargs):
        if (not await self._guard_lfg_channel(interaction, game_command)):
            return

        description = command_kwargs.pop("description", None)
        max_players = command_kwargs.pop("max_players", None)

        if all(value is None for value in (description, max_players, *command_kwargs.values())):
            # No arguments at all: same guided modal route as /lfg.
            await self._send_game_settings_modal(interaction, game_command)
            return

        parsed_parameters = {}
        for param_name, accepted_values in self.game_parameters.get(game_command, {}).items():
            raw_value = command_kwargs.get(param_name)
            if (raw_value is None):
                continue
            values, invalid = self._parse_param_values(raw_value, accepted_values)
            if (invalid is not None):
                await interaction.response.send_message(
                    f"Invalid value(s) for `{param_name}`: {', '.join(invalid)}.\n"
                    f"Valid values: {', '.join(accepted_values)}.",
                    ephemeral=True,
                )
                return
            parsed_parameters[param_name] = values

        await self._direct_lfg(interaction, game_command, description, max_players,
                               game_settings=parsed_parameters)
