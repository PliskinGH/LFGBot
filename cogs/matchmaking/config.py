"""Configuration loading for the matchmaking cog: games.ini and games_parameters.ini parsing."""

import configparser

from common import utils

from . import constants
from .models import GameOption, GuildGamesConfig


class ConfigMixin:
    """Configuration parsing helpers and guild config lookup."""

    def _load_game_parameters(self, parameters_config: configparser.ConfigParser):
        """Parse a game-parameters config into self.game_parameters.

        Each section is a game command; each key is a parameter name and the
        value is the comma-separated list of acceptable values for it.
        """
        if (parameters_config is None):
            return
        for game_command in parameters_config.sections():
            self.game_parameters[game_command] = {}
            for param_name, raw_values in parameters_config.items(game_command):
                values = [value.strip() for value in raw_values.split(",") if value.strip()]
                if (values):
                    self.game_parameters[game_command][param_name] = values

    def _load_guild_config(self, guild_config: GuildGamesConfig,
                           config: configparser.ConfigParser,
                           section: str):
        configdict = {}
        for arg in constants.CONFIG_GAMES_ARGS:
            configdict[arg] = utils.split_config_list(config.get(section, arg, fallback=None))
        for game in configdict[constants.CONFIG_GAMES_COMMANDS]:
            index = configdict[constants.CONFIG_GAMES_COMMANDS].index(game)
            game_option = GameOption(
                name=utils.safe_list_get(configdict[constants.CONFIG_GAMES_NAMES], index, ""),
                command=game,
                role=utils.safe_list_get(configdict[constants.CONFIG_GAMES_ROLES], index, ""),
                icon=utils.safe_list_get(configdict[constants.CONFIG_GAMES_ICONS], index, ""),
                color=utils.safe_list_get(configdict[constants.CONFIG_GAMES_COLORS], index, ""),
                forum=utils.safe_list_get(configdict[constants.CONFIG_GAMES_FORUMS], index, None),
                tag=utils.safe_list_get(configdict[constants.CONFIG_GAMES_TAGS], index, None),
                visibility=utils.safe_list_get(configdict[constants.CONFIG_GAMES_VISIBILITY], index, None),
                message=utils.safe_list_get(configdict[constants.CONFIG_GAMES_MESSAGES], index, None),
                registration_api=utils.safe_list_get(configdict[constants.CONFIG_GAMES_REGISTRATION_API], index, None),
                match_api=utils.safe_list_get(configdict[constants.CONFIG_GAMES_MATCH_API], index, None),
                match_url=utils.safe_list_get(configdict[constants.CONFIG_GAMES_MATCH_URL], index, None),
                api_token_env_var=utils.safe_list_get(configdict[constants.CONFIG_GAMES_API_TOKEN_ENV_VARS], index, None),
                website_url=utils.safe_list_get(configdict[constants.CONFIG_GAMES_WEBSITE_URL], index, None),
                registration_url=utils.safe_list_get(configdict[constants.CONFIG_GAMES_REGISTRATION_URL], index, None),
                profile_url=utils.safe_list_get(configdict[constants.CONFIG_GAMES_PROFILE_URL], index, None),
                default_max_guests=self.parse_default_max_guests(
                    utils.safe_list_get(configdict[constants.CONFIG_GAMES_MAX_PLAYERS], index, None)
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

