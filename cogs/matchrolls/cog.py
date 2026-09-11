"""The concrete MatchRolls cog, composing the feature mixins."""

import configparser

from discord.ext import commands

from .commands import RollsCommandsMixin
from .config import RollsConfigMixin
from .db_config import LoadedRollsConfig
from .help import RollsHelpMixin


class MatchRolls(RollsConfigMixin, RollsHelpMixin, RollsCommandsMixin,
                 commands.Cog):
    """Random rolls cog: /random over per-guild roll categories."""

    def __init__(self, bot: commands.Bot,
                 config: configparser.ConfigParser = None,
                 descriptions: list[dict] = None,
                 loaded_config: LoadedRollsConfig = None):
        self.bot = bot
        if (loaded_config is not None):
            # Pre-parsed configuration (e.g. from the database).
            self.default_categories = loaded_config.default_categories
            self.guilds = loaded_config.guilds
            self.descriptions = loaded_config.descriptions
        else:
            # File-based mode: parse the config files.
            self.default_categories, self.guilds = self._load_config(config)
            self.descriptions = descriptions or []
