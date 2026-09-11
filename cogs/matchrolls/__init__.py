"""Matchrolls cog package: /random rolls over per-guild roll categories."""

import configparser
import json

from discord.ext import commands

from . import constants, db_config
from .cog import MatchRolls
from .constants import RANDOM_COMMAND


async def setup(bot: commands.Bot):
    config = configparser.ConfigParser()
    config.read(constants.ROLLS_INI_PATH)
    with open(constants.ROLLS_DESCRIPTIONS_PATH, encoding="utf8") as json_file:
        descriptions = json.load(json_file)
    db = getattr(bot, "db", None)
    loaded = None
    if (db is not None):
        # Database mode: each stage is seeded when its table is empty
        # (descriptions after the items they reference), then loaded from
        # the database.
        if (await db_config.categories_empty()):
            await db_config.seed_categories_from_config(config)
        if (await db_config.items_empty()):
            await db_config.seed_items_from_config(config)
        if (await db_config.descriptions_empty()):
            await db_config.seed_descriptions_from_config(descriptions)
        loaded = await db_config.load_config_from_db()
    cog = MatchRolls(bot=bot, config=config, descriptions=descriptions,
                     loaded_config=loaded)
    await bot.add_cog(cog)
