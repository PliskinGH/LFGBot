"""The concrete Matchmaking cog, composing the feature mixins."""

import configparser

from discord.ext import commands

from common import constants

from .commands import CommandsMixin
from .config import ConfigMixin
from .db_config import LoadedConfig
from .guild_commands import GuildCommandsMixin
from .help import HelpMixin
from .interaction import InteractionMixin
from .match import MatchMixin
from .models import GuildGamesConfig


class Matchmaking(ConfigMixin, GuildCommandsMixin, HelpMixin,
                  InteractionMixin, MatchMixin, CommandsMixin,
                  commands.Cog):
    """LFG and game-thread management cog.

    ConfigMixin provides the config-parsing helpers; the other mixins add the
    slash commands, dynamic per-game commands, help, LFG interaction flow, and
    match/thread handling. discord.py's CogMeta collects the decorated commands
    from the whole MRO. Construction lives here, in the concrete cog class, so
    the mixins stay purely behavioral.
    """

    def __init__(self, bot: commands.Bot,
                 config : configparser.ConfigParser = None,
                 game_parameters : configparser.ConfigParser = None,
                 loaded_config : LoadedConfig = None):
        self.bot = bot
        # game_command -> { parameter_name -> { value: display_name } }
        self.game_parameters: dict[str, dict[str, dict[str, str]]] = {}
        # game_command -> { parameter_name -> match API field name }
        self.game_api_fields: dict[str, dict[str, str]] = {}
        # Fixed payload component field names from the [DEFAULT] section of
        # games_parameters.ini; inherited by every game (including games
        # without a section there), overridden by each game's own api_* keys.
        self.default_api_fields: dict[str, str] = {}
        if (loaded_config is not None):
            # Pre-parsed configuration (e.g. from the database): use as-is.
            self.guilds = loaded_config.guilds
            self.default_guild_config = loaded_config.default_guild_config
            self.game_parameters = loaded_config.game_parameters
            self.game_api_fields = loaded_config.game_api_fields
            self.default_api_fields = loaded_config.default_api_fields
        else:
            # File-based mode: parse the config files.
            self.guilds : dict[int, GuildGamesConfig] = {} # dict of guild_id -> GuildGamesConfig
            self.default_guild_config = GuildGamesConfig(None)
            self._load_guild_config(self.default_guild_config, config, constants.CONFIG_DEFAULT)
            for guild in config.sections():
                guild_id = config.getint(guild, constants.CONFIG_ID, fallback=None)
                if (guild_id is None):
                    continue
                guild_config = GuildGamesConfig(guild_id)
                self._load_guild_config(guild_config, config, guild)
                self.guilds[guild_id] = guild_config
            self._load_game_parameters(game_parameters)

        # match_api_url -> set of multi-value field names (from API metadata)
        self._match_api_metadata: dict[str, set[str]] = {}

