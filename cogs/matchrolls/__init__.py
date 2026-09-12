"""Matchrolls cog package: /random rolls over per-guild roll categories."""

import configparser
import json

from discord.ext import commands

from . import constants, db_config
from .cog import MatchRolls
from .constants import RANDOM_COMMAND, ROLLSETS_COMMAND


async def setup(bot: commands.Bot):
    config = configparser.ConfigParser()
    config.read(constants.ROLLS_INI_PATH)
    with open(constants.ROLLS_DESCRIPTIONS_PATH, encoding="utf8") as json_file:
        descriptions = json.load(json_file)
    db = getattr(bot, "db", None)
    loaded = None
    if (db is not None):
        seeded = False
        if (await db_config.categories_empty()):
            print("Matchrolls: seeding roll categories from the config file...")
            await db_config.seed_categories_from_config(config)
            print("Matchrolls: roll categories seeded.")
            seeded = True
        if (await db_config.items_empty()):
            print("Matchrolls: seeding roll items from the config file...")
            await db_config.seed_items_from_config(config)
            print("Matchrolls: roll items seeded.")
            seeded = True
        if (await db_config.descriptions_empty()):
            print("Matchrolls: seeding roll descriptions from the config file...")
            await db_config.seed_descriptions_from_config(descriptions)
            print("Matchrolls: roll descriptions seeded.")
            seeded = True
        if (not seeded):
            print("Matchrolls: loading roll configuration from the database.")
        loaded = await db_config.load_config_from_db()
    cog = MatchRolls(bot=bot, config=config, descriptions=descriptions,
                     loaded_config=loaded)
    await bot.add_cog(cog)
