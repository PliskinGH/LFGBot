"""Matchmaking cog package: /lfg, /rename, and per-game slash commands.

Kept as a package so the single-file cog can be split into focused mixins:
constants, models, views, config parsing, dynamic guild command registration,
help text, the LFG interaction flow, match/thread handling, and the slash
commands. The concrete ``Matchmaking`` class composes them all.

``setup()`` is the extension entry point used by ``load_extension``; it is the
only thing this module defines. Everything else is imported from its specific
submodule (``cogs.matchmaking.cog``, ``cogs.matchmaking.constants``, ...),
keeping the package boundary thin.
"""

import configparser

from discord.ext import commands

from . import constants
from . import db_config
from .cog import Matchmaking
from .views import LFGView


async def setup(bot: commands.Bot):
    config = configparser.ConfigParser()
    config.read(constants.GAMES_INI_PATH)
    game_parameters = configparser.ConfigParser()
    game_parameters.read(constants.GAMES_PARAMETERS_PATH)
    db = getattr(bot, "db", None)
    loaded = None
    if (db is not None):
        # Database mode: seeded from the config files on first initialization,
        # then the source of truth.
        if (db.fresh):
            await db_config.seed_db_from_config(config, game_parameters)
        loaded = await db_config.load_config_from_db()
    cog = Matchmaking(bot=bot, config=config,
                      game_parameters=game_parameters, loaded_config=loaded)
    await bot.add_cog(cog)
    bot.add_view(LFGView(cog=cog))
    # Register one guild-specific slash command per configured game. These are
    # synced per guild by the bot's setup_hook after all cogs are loaded.
    cog.register_guild_commands()

