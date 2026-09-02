"""Configuration loading for the matchmaking cog: games.ini parsing and config lookup."""

import configparser

from common.utils import safe_list_get, split_config_list

from . import constants
from . import utils
from .models import GameOption, GuildGamesConfig, ParameterDefinition


class ConfigMixin:
    """Configuration parsing helpers and guild config lookup."""

    def _load_game_parameters(self, parameters_config: configparser.ConfigParser):
        """Parse the game-parameters config into self.game_parameters et al.

        See ``utils.parse_game_parameters`` (shared with the database
        seeding path) for the format and the returned maps.

        Parameters are keyed by guild id (``DEFAULT_GUILD_ID`` holds the
        config-file definitions, shared by every guild; guilds materialize
        their own copies once edited). ``get_game_parameters`` resolves a
        specific guild, falling back to the defaults.
        """
        (game_parameters, game_api_fields,
         default_api_fields) = utils.parse_game_parameters(parameters_config)
        self.game_parameters = {constants.DEFAULT_GUILD_ID: game_parameters}
        self.game_api_fields = {constants.DEFAULT_GUILD_ID: game_api_fields}
        self.default_api_fields = default_api_fields

    def get_game_parameters(self, guild_id: int,
                            game_command) -> dict[str, ParameterDefinition]:
        """A game's parameter set: ``{param_name: {"display_name": label,
        "values": {value: display_name}}}``.

        Falls back to the DEFAULT definitions for guilds without their own
        entry (they inherit the config-file parameters).
        """
        return (self.game_parameters
                .get(guild_id,
                     self.game_parameters.get(constants.DEFAULT_GUILD_ID, {}))
                .get(game_command, {}))

    def get_game_parameter_values(self, guild_id: int, game_command,
                                  param_name: str) -> dict[str, str]:
        """The ``{value: display_name}`` map of one parameter (empty when unknown)."""
        return (self.get_game_parameters(guild_id, game_command)
                .get(param_name, {}).get("values", {}))

    def get_game_api_fields(self, guild_id: int,
                            game_command) -> dict[str, str]:
        """The match API field names of a game's parameters/overrides."""
        return (self.game_api_fields
                .get(guild_id,
                     self.game_api_fields.get(constants.DEFAULT_GUILD_ID, {}))
                .get(game_command, {}))

    @staticmethod
    def _load_guild_config(guild_config: GuildGamesConfig,
                           config: configparser.ConfigParser,
                           section: str):
        configdict = {}
        for arg in constants.CONFIG_GAMES_ARGS:
            configdict[arg] = split_config_list(config.get(section, arg, fallback=None))
        for game in configdict[constants.CONFIG_GAMES_COMMANDS]:
            index = configdict[constants.CONFIG_GAMES_COMMANDS].index(game)
            game_option = GameOption(
                name=safe_list_get(configdict[constants.CONFIG_GAMES_NAMES], index, ""),
                command=game,
                role=safe_list_get(configdict[constants.CONFIG_GAMES_ROLES], index, ""),
                icon=safe_list_get(configdict[constants.CONFIG_GAMES_ICONS], index, ""),
                color=safe_list_get(configdict[constants.CONFIG_GAMES_COLORS], index, ""),
                forum=safe_list_get(configdict[constants.CONFIG_GAMES_FORUMS], index, None),
                tag=safe_list_get(configdict[constants.CONFIG_GAMES_TAGS], index, None),
                visibility=safe_list_get(configdict[constants.CONFIG_GAMES_VISIBILITY], index, None),
                message=safe_list_get(configdict[constants.CONFIG_GAMES_MESSAGES], index, None),
                registration_api=safe_list_get(configdict[constants.CONFIG_GAMES_REGISTRATION_API], index, None),
                match_api=safe_list_get(configdict[constants.CONFIG_GAMES_MATCH_API], index, None),
                match_url=safe_list_get(configdict[constants.CONFIG_GAMES_MATCH_URL], index, None),
                api_token_env_var=safe_list_get(configdict[constants.CONFIG_GAMES_API_TOKEN_ENV_VARS], index, None),
                website_url=safe_list_get(configdict[constants.CONFIG_GAMES_WEBSITE_URL], index, None),
                registration_url=safe_list_get(configdict[constants.CONFIG_GAMES_REGISTRATION_URL], index, None),
                profile_url=safe_list_get(configdict[constants.CONFIG_GAMES_PROFILE_URL], index, None),
                default_max_guests=ConfigMixin.parse_default_max_guests(
                    safe_list_get(configdict[constants.CONFIG_GAMES_MAX_PLAYERS], index, None)
                )
            )
            guild_config.games[game] = game_option

    def get_guild_config(self, guild_id: int) -> GuildGamesConfig:
        return self.guilds.get(guild_id, self.default_guild_config)

    @staticmethod
    def parse_default_max_guests(max_players : str | None) -> int | None:
        if (not max_players):
            return None
        try:
            parsed_value = int(max_players)
        except ValueError:
            return None
        return parsed_value - 1 if 2 <= parsed_value <= 100 else None

