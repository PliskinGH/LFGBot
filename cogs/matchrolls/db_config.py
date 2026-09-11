"""INI/JSON <-> database mapping for the matchrolls cog's configuration.

With ``DATABASE_URL`` set, the database is the source of truth: each stage
(roll categories, items, descriptions) is seeded from the config files when
its table is empty, then loaded from the database. ``LoadedRollsConfig``
mirrors what ``MatchRolls`` consumes, so the cog uses both sources
identically, preserving the config files' ordering.
"""

import configparser

from common import utils as common_utils

from tortoise.transactions import in_transaction

from db import models

from . import constants
from .config import RollsConfigMixin


class LoadedRollsConfig:
    """The rolls configuration structures ``MatchRolls`` consumes."""

    def __init__(self):
        # category -> verbatim comma-separated set ([DEFAULT] section).
        self.default_categories: dict[str, str] = {}
        # guild_id -> {category: verbatim set} ([DEFAULT] inherited).
        self.guilds: dict[int, dict[str, str]] = {}
        # Roll description embeds, verbatim from rolls_descriptions.json.
        self.descriptions: list[dict] = []


def loaded_config_from_ini(config: configparser.ConfigParser,
                           descriptions: list[dict]) -> LoadedRollsConfig:
    """Parse the rolls config files into a LoadedRollsConfig (file-based mode)."""
    loaded = LoadedRollsConfig()
    (loaded.default_categories,
     loaded.guilds) = RollsConfigMixin._load_config(config)
    loaded.descriptions = list(descriptions)
    return loaded


async def categories_empty() -> bool:
    """Whether the categories table holds no configuration yet."""
    return (await models.RollCategory.all().count() == 0)


async def items_empty() -> bool:
    """Whether the items table holds no configuration yet."""
    return (await models.RollItem.all().count() == 0)


async def descriptions_empty() -> bool:
    """Whether the descriptions table holds no configuration yet."""
    return (await models.RollDescription.all().count() == 0)


async def is_empty() -> bool:
    """Whether the categories table holds no configuration yet."""
    return await categories_empty()


# --------------------------------------------------------------------------- #
# Seeding (config files -> rows)
# --------------------------------------------------------------------------- #

async def seed_categories_from_config(config: configparser.ConfigParser) -> None:
    """Seed the category rows from rolls.ini in one transaction."""
    (default_categories,
     guilds) = RollsConfigMixin._load_config(config)
    async with in_transaction():
        await _seed_guild_categories(constants.DEFAULT_GUILD_ID,
                                     default_categories)
        for guild_id, categories in guilds.items():
            await _seed_guild_categories(guild_id, categories)


async def seed_items_from_config(config: configparser.ConfigParser) -> None:
    """Seed the item rows from rolls.ini in one transaction.

    Each category's verbatim set is split into one item per name; items
    anchor to the [DEFAULT] guild's category rows (descriptions are shared).
    """
    (default_categories,
     guilds) = RollsConfigMixin._load_config(config)
    async with in_transaction():
        for name in default_categories:
            await _seed_category_items(constants.DEFAULT_GUILD_ID, name)
        for guild_id, categories in guilds.items():
            for name in categories:
                await _seed_category_items(guild_id, name)


async def seed_descriptions_from_config(descriptions: list[dict]) -> None:
    """Seed the description rows from rolls_descriptions.json in one
    transaction.

    Each entry resolves to an item of the [DEFAULT] guild via its
    ``category`` label and ``title``; entries whose title does not exist
    under the labelled category are skipped (a seeding consistency check).
    """
    async with in_transaction():
        for entry in descriptions:
            await _seed_description(entry)


async def seed_db_from_config(config: configparser.ConfigParser,
                              descriptions: list[dict]) -> None:
    """Seed all three stages from the config files (empty tables assumed)."""
    await seed_categories_from_config(config)
    await seed_items_from_config(config)
    await seed_descriptions_from_config(descriptions)


async def _seed_guild_categories(guild_id: int,
                                 categories: dict[str, str]) -> None:
    """Create the guild row if needed (shared with the games config) and
    one category row per configured category."""
    if (not categories):
        return
    guild, _ = await models.Guild.get_or_create(guild_id=guild_id)
    for name, items in categories.items():
        await models.RollCategory.create(guild=guild, name=name, items=items)


async def _seed_category_items(guild_id: int, category_name: str) -> None:
    """Create one item row per name of the category's verbatim set."""
    category = await models.RollCategory.get_or_none(
        guild__guild_id=guild_id, name=category_name)
    if (category is None):
        return
    for name in common_utils.split_config_list(category.items):
        await models.RollItem.create(category=category, name=name)


async def _seed_description(entry: dict) -> None:
    """Create one description row for a JSON entry; skip it when its
    ``category``/``title`` pair resolves to no item."""
    category = await models.RollCategory.get_or_none(
        guild__guild_id=constants.DEFAULT_GUILD_ID,
        name=str(entry.get("category", "")).lower())
    if (category is None):
        return
    item = await models.RollItem.get_or_none(
        category=category, name=entry.get("title"))
    if (item is None):
        return
    await models.RollDescription.create(
        item=item, description=entry.get("description") or "",
        color=entry.get("color"),
        image_url=(entry.get("image") or {}).get("url"),
        thumbnail_url=(entry.get("thumbnail") or {}).get("url"))


# --------------------------------------------------------------------------- #
# Loading (rows -> LoadedRollsConfig)
# --------------------------------------------------------------------------- #

async def load_config_from_db() -> LoadedRollsConfig:
    """Load the rolls configuration into a LoadedRollsConfig, ordered by
    insertion id (config-file order)."""
    loaded = LoadedRollsConfig()
    guilds = await models.Guild.all().order_by("guild_id")
    categories = await models.RollCategory.all().order_by("id")
    items = (await models.RollItem.all().order_by("id")
             .select_related("category"))
    descriptions = (await models.RollDescription.all().order_by("id")
                    .select_related("item", "item__category"))

    categories_by_guild: dict[int, list[models.RollCategory]] = {}
    for category in categories:
        categories_by_guild.setdefault(category.guild_id, []).append(category)

    items_by_category: dict[int, list[models.RollItem]] = {}
    for item in items:
        items_by_category.setdefault(item.category_id, []).append(item)

    # Guilds without category rows (games-only) fall back to [DEFAULT].
    for guild in guilds:
        guild_categories = categories_by_guild.get(guild.guild_id, [])
        if (not guild_categories):
            continue
        roll_sets = {category.name: category.items
                     for category in guild_categories}
        if (guild.guild_id == constants.DEFAULT_GUILD_ID):
            loaded.default_categories = roll_sets
        else:
            loaded.guilds[guild.guild_id] = roll_sets

    # Descriptions are rebuilt as the embed dicts the cog consumes (the
    # JSON shape: the item name is the title, the category name its label;
    # blank parts are omitted, as in the JSON).
    for description in descriptions:
        item = description.item
        category_name = item.category.name
        embed = {
            "title": item.name,
            "category": category_name.capitalize(),
        }
        if (description.description):
            embed["description"] = description.description
        if (description.color is not None):
            embed["color"] = description.color
        if (description.image_url):
            embed["image"] = {"url": description.image_url}
        if (description.thumbnail_url):
            embed["thumbnail"] = {"url": description.thumbnail_url}
        loaded.descriptions.append(embed)
    return loaded
