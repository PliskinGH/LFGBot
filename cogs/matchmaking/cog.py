"""The concrete Matchmaking cog, composing the feature mixins."""

import configparser
import re

from discord.ext import commands

from common import common

from .commands import CommandsMixin
from .config import ConfigMixin
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
                 game_parameters : configparser.ConfigParser = None):
        self.bot = bot
        self.guilds : dict[int, GuildGamesConfig] = {} # dict of guild_id -> GuildGamesConfig
        self.default_guild_config = GuildGamesConfig(None)
        self._load_guild_config(self.default_guild_config, config, common.CONFIG_DEFAULT)
        for guild in config.sections():
            guild_id = config.getint(guild, common.CONFIG_ID, fallback=None)
            if (guild_id is None):
                continue
            guild_config = GuildGamesConfig(guild_id)
            self._load_guild_config(guild_config, config, guild)
            self.guilds[guild_id] = guild_config

        self.custom_emoji_re = re.compile(r"<:[\w]+:[\d]+>")

        # game_command -> { parameter_name -> [accepted values] }
        self.game_parameters: dict[str, dict[str, list[str]]] = {}
        self._load_game_parameters(game_parameters)

