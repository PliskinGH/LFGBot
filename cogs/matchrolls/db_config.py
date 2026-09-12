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
        # The [DEFAULT] guild's description embeds, inherited by every guild.
        self.default_descriptions: list[dict] = []
        # guild_id -> its own description embeds (defaults inherited at runtime).
        self.guild_descriptions: dict[int, list[dict]] = {}


def loaded_config_from_ini(config: configparser.ConfigParser,
                           descriptions: list[dict]) -> LoadedRollsConfig:
    """Parse the rolls config files into a LoadedRollsConfig (file-based mode)."""
    loaded = LoadedRollsConfig()
    (loaded.default_categories,
     loaded.guilds) = RollsConfigMixin._load_config(config)
    loaded.default_descriptions = list(descriptions)
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
    """Seed the active item rows from rolls.ini in one transaction.

    Each category is seeded from its verbatim set; items anchor to the
    [DEFAULT] guild's category rows (descriptions are shared).
    """
    (default_categories,
     guilds) = RollsConfigMixin._load_config(config)
    async with in_transaction():
        await _seed_category_items(constants.DEFAULT_GUILD_ID,
                                   default_categories)
        for guild_id, categories in guilds.items():
            await _seed_category_items(guild_id, categories)


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
    for name in categories:
        await models.RollCategory.create(guild=guild, name=name)


async def _seed_category_items(guild_id: int,
                               categories: dict[str, str]) -> None:
    """Create one active item row per name of each category's set."""
    for name, items in categories.items():
        category = await models.RollCategory.get_or_none(
            guild__guild_id=guild_id, name=name)
        if (category is None):
            continue
        for item_name in common_utils.split_config_list(items):
            await models.RollItem.create(category=category, name=item_name)


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


def _description_embed(description: models.RollDescription) -> dict:
    """The embed dict a description row renders as (the JSON shape)."""
    embed = {
        "title": description.item.name,
        "category": description.item.category.name.capitalize(),
    }
    if (description.description):
        embed["description"] = description.description
    if (description.color is not None):
        embed["color"] = description.color
    if (description.image_url):
        embed["image"] = {"url": description.image_url}
    if (description.thumbnail_url):
        embed["thumbnail"] = {"url": description.thumbnail_url}
    return embed


async def description_variant_embeds(guild_id: int, item_name: str,
                                     ) -> list[dict]:
    """The item's description variants as embed dicts, in insertion order."""
    return [_description_embed(description)
            for description in await description_variants(guild_id, item_name)]


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

    active_names_by_category: dict[int, list[str]] = {}
    for item in items:
        if (not item.active):
            continue
        active_names_by_category.setdefault(item.category_id, []).append(item.name)

    # Guilds without category rows (games-only) fall back to [DEFAULT].
    for guild in guilds:
        guild_categories = categories_by_guild.get(guild.guild_id, [])
        if (not guild_categories):
            continue
        roll_sets = {category.name: ", ".join(
            active_names_by_category.get(category.id, []))
            for category in guild_categories}
        if (guild.guild_id == constants.DEFAULT_GUILD_ID):
            loaded.default_categories = roll_sets
        else:
            loaded.guilds[guild.guild_id] = roll_sets

    # Descriptions are rebuilt as the embed dicts the cog consumes and
    # grouped by the owning guild.
    for description in descriptions:
        guild_id = description.item.category.guild_id
        if (guild_id == constants.DEFAULT_GUILD_ID):
            loaded.default_descriptions.append(_description_embed(description))
        else:
            loaded.guild_descriptions.setdefault(guild_id, []).append(
                _description_embed(description))
    return loaded
# --------------------------------------------------------------------------- #
# Admin configuration (guild edits -> rows)
# --------------------------------------------------------------------------- #

async def effective_category_sets(guild_id: int) -> dict[str, str]:
    """A guild's effective ``{category: set}`` map (its own, else [DEFAULT])."""
    loaded = await load_config_from_db()
    return loaded.guilds.get(guild_id, loaded.default_categories)


async def ensure_guild_categories(guild_id: int) -> None:
    """Materialize the guild's full roll configuration (categories, their items,
    and the items' descriptions) from the [DEFAULT] config. A no-op once the
    guild owns any category row."""
    if (guild_id == constants.DEFAULT_GUILD_ID):
        return
    if (await models.RollCategory.filter(
            guild__guild_id=guild_id).count() > 0):
        return
    default_categories = (await models.RollCategory
                          .filter(guild__guild_id=constants.DEFAULT_GUILD_ID)
                          .order_by("id").prefetch_related("roll_items"))
    async with in_transaction():
        guild, _ = await models.Guild.get_or_create(guild_id=guild_id)
        for category in default_categories:
            replica = await models.RollCategory.create(
                guild=guild, name=category.name)
            for item in await category.roll_items.all().order_by("id"):
                item_replica = await models.RollItem.create(
                    category=replica, name=item.name, active=item.active)
                for description in await models.RollDescription.filter(
                        item=item).order_by("id"):
                    await models.RollDescription.create(
                        item=item_replica,
                        description=description.description,
                        color=description.color,
                        image_url=description.image_url,
                        thumbnail_url=description.thumbnail_url)


async def _guild_has_own_categories(guild_id: int) -> bool:
    """Whether the guild owns any category row (i.e. has been materialized)."""
    if (guild_id == constants.DEFAULT_GUILD_ID):
        return True
    return (await models.RollCategory.filter(
        guild__guild_id=guild_id).count() > 0)


async def _items_for_read(guild_id: int, category_name: str | None = None
                          ) -> list[models.RollItem]:
    """Item rows visible to a guild: its own if materialized, else [DEFAULT]'s."""
    source = guild_id if (await _guild_has_own_categories(guild_id)) \
        else constants.DEFAULT_GUILD_ID
    query = models.RollItem.filter(category__guild__guild_id=source)
    if (category_name is not None):
        query = query.filter(category__name=category_name)
    return await query.order_by("id")


async def _descriptions_for_read(guild_id: int, item_name: str
                                 ) -> list[models.RollDescription]:
    """Description rows visible to a guild: its own if materialized, else
    [DEFAULT]'s."""
    source = guild_id if (await _guild_has_own_categories(guild_id)) \
        else constants.DEFAULT_GUILD_ID
    return await models.RollDescription.filter(
        item__category__guild__guild_id=source, item__name=item_name
    ).order_by("id").select_related("item", "item__category")


async def list_category_items(guild_id: int, category_name: str,
                              ) -> tuple[list[str], list[str]]:
    """The category's (active, inactive) item names in insertion order."""
    items = await _items_for_read(guild_id, category_name)
    active = [item.name for item in items if item.active]
    inactive = [item.name for item in items if not item.active]
    return active, inactive


async def add_category(guild_id: int, category_name: str,
                       item_names: list[str]) -> bool:
    """Add a category to a guild's config (materializing it first); whether
    it was created."""
    await ensure_guild_categories(guild_id)
    if (await models.RollCategory.get_or_none(
            guild__guild_id=guild_id, name=category_name) is not None):
        return False
    async with in_transaction():
        category = await models.RollCategory.create(
            guild_id=guild_id, name=category_name)
        for item_name in item_names:
            await models.RollItem.create(category=category, name=item_name)
    return True


async def update_category(guild_id: int, category_name: str,
                          item_names: list[str] | None = None,
                          new_name: str | None = None) -> bool:
    """Rename a category and/or reconcile its item set; whether it existed.

    Names in ``item_names`` that are not configured yet are added (reactivating
    a previously removed name restores its descriptions); names dropped from
    the set become inactive (rows and descriptions preserved).
    """
    await ensure_guild_categories(guild_id)
    category = await models.RollCategory.get_or_none(
        guild__guild_id=guild_id, name=category_name)
    if (category is None):
        return False
    if (new_name is not None and new_name != category_name):
        if (await models.RollCategory.get_or_none(
                guild__guild_id=guild_id, name=new_name) is not None):
            return False
        category.name = new_name
        await category.save(update_fields=["name"])
    if (item_names is not None):
        await _reconcile_items(category, item_names)
    return True


async def _reconcile_items(category: models.RollCategory,
                           item_names: list[str]) -> None:
    """Make ``item_names`` the category's active items, preserving rows."""
    registry = {item.name: item
                for item in await models.RollItem.filter(category=category)}
    for name in item_names:
        item = registry.get(name)
        if (item is None):
            await models.RollItem.create(category=category, name=name)
        elif (not item.active):
            item.active = True
            await item.save(update_fields=["active"])
    for item in registry.values():
        if (item.name not in item_names and item.active):
            item.active = False
            await item.save(update_fields=["active"])


async def delete_category(guild_id: int, category_name: str) -> bool:
    """Delete a category (cascading its items and their descriptions);
    whether it existed."""
    await ensure_guild_categories(guild_id)
    category = await models.RollCategory.get_or_none(
        guild__guild_id=guild_id, name=category_name)
    if (category is None):
        return False
    await category.delete()
    return True


async def item_variant_counts(guild_id: int, category_name: str | None = None,
                              ) -> list[tuple[str, int, bool]]:
    """The guild's ordered (item, variant count, active) rows, optionally
    restricted to one category."""
    items = await _items_for_read(guild_id, category_name)
    counts: list[tuple[str, int, bool]] = []
    for item in items:
        count = await models.RollDescription.filter(item=item).count()
        counts.append((item.name, count, item.active))
    return counts


async def active_item_names(guild_id: int) -> list[str]:
    """The guild's active item names across its categories."""
    items = await _items_for_read(guild_id)
    return [item.name for item in items if item.active]


async def add_description(guild_id: int, item_name: str,
                          fields: dict) -> bool:
    """Add a description variant to a guild-owned active item; whether added.

    ``fields`` maps model attribute to value (description, color, image_url,
    thumbnail_url); a None value leaves the attribute unset.
    """
    await ensure_guild_categories(guild_id)
    item = await models.RollItem.get_or_none(
        category__guild__guild_id=guild_id, name=item_name, active=True)
    if (item is None):
        return False
    await models.RollDescription.create(item=item, **fields)
    return True


async def description_variants(guild_id: int, item_name: str,
                               ) -> list[models.RollDescription]:
    """The item's description variants in insertion order ([] when unknown)."""
    return await _descriptions_for_read(guild_id, item_name)


async def update_description(guild_id: int, item_name: str, variant: int,
                             fields: dict) -> bool:
    """Update one variant (1-based) of an item's descriptions; whether it
    existed. Only the keys of ``fields`` are changed (None clears the value)."""
    await ensure_guild_categories(guild_id)
    variants = await description_variants(guild_id, item_name)
    if (variant < 1 or variant > len(variants)):
        return False
    if (fields):
        await models.RollDescription.filter(
            id=variants[variant - 1].id).update(**fields)
    return True


async def delete_description(guild_id: int, item_name: str,
                             variant: int) -> bool:
    """Delete one variant (1-based) of an item's descriptions; whether it
    existed."""
    await ensure_guild_categories(guild_id)
    variants = await description_variants(guild_id, item_name)
    if (variant < 1 or variant > len(variants)):
        return False
    await variants[variant - 1].delete()
    return True
