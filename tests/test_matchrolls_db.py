"""Tests for the database-backed rolls configuration (cogs/matchrolls/db_config.py)."""

from db import models

from cogs.matchmaking import db_config as matchmaking_db_config
from cogs.matchrolls import db_config
from cogs.matchrolls.cog import MatchRolls
from cogs.matchrolls.constants import DEFAULT_GUILD_ID

from tests.conftest import FakeBot


def _assert_same_config(expected, actual):
    assert actual.default_categories == expected.default_categories
    assert set(actual.guilds.keys()) == set(expected.guilds.keys())
    for guild_id, categories in expected.guilds.items():
        assert actual.guilds[guild_id] == categories
    assert actual.default_descriptions == expected.default_descriptions
    assert actual.guild_descriptions == expected.guild_descriptions


class TestLoadedRollsConfigFromIni:
    def test_matches_file_parsing(self, rolls_config, descriptions):
        loaded = db_config.loaded_config_from_ini(rolls_config, descriptions)
        assert list(loaded.default_categories.keys()) == ["map", "landmark"]
        assert loaded.default_categories["map"] == "Alpha, Beta, Gamma"
        # GuildB's map override, with the [DEFAULT] landmark inherited; its
        # ID key is not a category.
        assert set(loaded.guilds.keys()) == {42424}
        assert list(loaded.guilds[42424].items()) == [
            ("map", "Zeta, Eta"), ("landmark", "Delta, Epsilon")]
        assert "id" not in loaded.guilds[42424]
        assert loaded.default_descriptions == descriptions
        assert loaded.guild_descriptions == {}


class TestCogFromLoadedRollsConfig:
    """A cog built from a LoadedRollsConfig matches the file-parsing cog."""

    def test_attributes_match_file_parsing(self, rolls_config, descriptions):
        from_files = MatchRolls(bot=FakeBot(), config=rolls_config,
                                descriptions=descriptions)
        loaded = db_config.loaded_config_from_ini(rolls_config, descriptions)
        from_loaded = MatchRolls(bot=FakeBot(), loaded_config=loaded)
        assert from_loaded.default_categories == from_files.default_categories
        assert from_loaded.guilds == from_files.guilds
        assert from_loaded.default_descriptions == from_files.default_descriptions
        assert from_loaded.guild_descriptions == from_files.guild_descriptions

    def test_get_roll_sets_falls_back_to_default(self, rolls_config, descriptions):
        loaded = db_config.loaded_config_from_ini(rolls_config, descriptions)
        cog = MatchRolls(bot=FakeBot(), loaded_config=loaded)
        # Unknown guilds (and None, e.g. interactions outside a guild) fall
        # back to the [DEFAULT] categories.
        assert cog.get_roll_sets(None) == cog.default_categories
        assert cog.get_roll_sets(999999) == cog.default_categories
        assert cog.get_roll_sets(42424) == cog.guilds[42424]
        assert cog.get_roll_sets(42424)["map"] == "Zeta, Eta"


class TestRollsInitialize:
    async def test_empty_tables_are_empty(self, db):
        assert await db_config.categories_empty() is True
        assert await db_config.items_empty() is True
        assert await db_config.descriptions_empty() is True
        assert await db_config.is_empty() is True

    async def test_seeded_tables_are_not_empty(self, db, rolls_config,
                                               descriptions):
        await db_config.seed_db_from_config(rolls_config, descriptions)
        assert await db_config.categories_empty() is False
        assert await db_config.items_empty() is False
        assert await db_config.descriptions_empty() is False
        assert await db_config.is_empty() is False


class TestRollsSeeding:
    async def test_staged_seeding_fills_each_table(self, db, rolls_config,
                                                   descriptions):
        await db_config.seed_categories_from_config(rolls_config)
        assert await models.RollCategory.all().count() == 4
        assert await models.RollItem.all().count() == 0

        await db_config.seed_items_from_config(rolls_config)
        assert await models.RollItem.all().count() == 9
        assert await models.RollDescription.all().count() == 0

        await db_config.seed_descriptions_from_config(descriptions)
        assert await models.RollDescription.all().count() == 5

    async def test_seeding_passes_do_not_duplicate(self, db, rolls_config,
                                                   descriptions):
        await db_config.seed_db_from_config(rolls_config, descriptions)
        # A second guarded pass (as setup() re-evaluates the emptiness
        # checks on the next startup) must create no duplicates.
        if (await db_config.categories_empty()):
            await db_config.seed_categories_from_config(rolls_config)
        if (await db_config.items_empty()):
            await db_config.seed_items_from_config(rolls_config)
        if (await db_config.descriptions_empty()):
            await db_config.seed_descriptions_from_config(descriptions)
        assert await models.RollCategory.all().count() == 4
        assert await models.RollItem.all().count() == 9
        assert await models.RollDescription.all().count() == 5

    async def test_round_trip_matches_file_parsing(self, db, rolls_config,
                                                   descriptions):
        expected = db_config.loaded_config_from_ini(rolls_config, descriptions)
        await db_config.seed_db_from_config(rolls_config, descriptions)
        actual = await db_config.load_config_from_db()
        _assert_same_config(expected, actual)

    async def test_rows_preserve_order_and_values(self, db, rolls_config,
                                                  descriptions):
        await db_config.seed_db_from_config(rolls_config, descriptions)
        categories = await models.RollCategory.all().order_by("id")
        items = (await models.RollItem.all().order_by("id")
                 .select_related("category"))
        description_rows = (await models.RollDescription.all().order_by("id")
                            .select_related("item", "item__category"))
        # [DEFAULT] under the sentinel guild id 0, then GuildB's categories.
        assert [(category.guild_id, category.name)
                for category in categories] == [
            (DEFAULT_GUILD_ID, "map"),
            (DEFAULT_GUILD_ID, "landmark"),
            (42424, "map"),
            (42424, "landmark")]
        # One active item per category set name, FK to its category row:
        # [DEFAULT] map and landmark, then GuildB's map override and its
        # inherited landmark.
        assert [(item.category.name, item.name, item.active)
                for item in items] == [
            ("map", "Alpha", True), ("map", "Beta", True),
            ("map", "Gamma", True),
            ("landmark", "Delta", True), ("landmark", "Epsilon", True),
            ("map", "Zeta", True), ("map", "Eta", True),
            ("landmark", "Delta", True), ("landmark", "Epsilon", True)]
        # Descriptions FK their item; blank/unset parts stay blank/unset.
        assert [(row.item.name, row.description, row.color,
                 row.image_url, row.thumbnail_url)
                for row in description_rows] == [
            ("Alpha", "", 14520159, None, None),
            ("Beta", "", 16514303, None, None),
            ("Gamma", "", 1752220, None, None),
            ("Delta", "", 5127742, None, None),
            ("Epsilon", "", 11427369, None, None)]

    async def test_description_seeding_consistency_check(self, db,
                                                         rolls_config):
        # Entries whose category label matches no category, or whose title
        # is not an item of the labelled category, are skipped; the rest
        # is seeded.
        entries = [
            {"title": "Alpha", "category": "Map", "color": 14520159},
            {"title": "Zeta", "category": "Map", "color": 1},  # not [DEFAULT]
            {"title": "Gamma", "category": "Deck"},  # no such category
        ]
        await db_config.seed_categories_from_config(rolls_config)
        await db_config.seed_items_from_config(rolls_config)
        await db_config.seed_descriptions_from_config(entries)
        rows = (await models.RollDescription.all().order_by("id")
                .select_related("item"))
        assert [(row.item.name, row.color) for row in rows] == [
            ("Alpha", 14520159)]
        assert await db_config.descriptions_empty() is False

    async def test_guild_rows_are_shared_with_the_games_cog(
            self, db, games_config, game_parameters_config,
            rolls_config, descriptions):
        # The matchmaking cog seeds first; the rolls seeding must reuse its
        # guild rows instead of creating conflicting ones.
        await matchmaking_db_config.seed_db_from_config(
            games_config, game_parameters_config)
        await db_config.seed_db_from_config(rolls_config, descriptions)
        guilds = await models.Guild.all().order_by("guild_id")
        assert [guild.guild_id for guild in guilds] == [0, 42424, 90401]
        loaded = await db_config.load_config_from_db()
        # Games-only guilds fall back to the [DEFAULT] categories.
        assert 90401 not in loaded.guilds
        assert loaded.default_categories["map"] == "Alpha, Beta, Gamma"

    async def test_cog_loaded_from_db_matches_file_cog(self, db, rolls_config,
                                                       descriptions):
        await db_config.seed_db_from_config(rolls_config, descriptions)
        loaded = await db_config.load_config_from_db()
        from_files = MatchRolls(bot=FakeBot(), config=rolls_config,
                                descriptions=descriptions)
        from_db = MatchRolls(bot=FakeBot(), loaded_config=loaded)
        assert from_db.get_roll_sets(1) == from_files.get_roll_sets(1)
        assert from_db.get_roll_sets(42424) == from_files.get_roll_sets(42424)
        assert from_db.get_descriptions(1) == from_files.get_descriptions(1)
        assert from_db.get_descriptions(42424) == from_files.get_descriptions(42424)

    async def test_non_materialized_guild_inherits_default_descriptions(
            self, db, rolls_config, descriptions):
        # Only the [DEFAULT] section is seeded: guild 1 has no own rows.
        await db_config.seed_db_from_config(rolls_config, descriptions)
        loaded = await db_config.load_config_from_db()
        # Guild 1 is not materialized → it has no own descriptions.
        assert 1 not in loaded.guild_descriptions
        assert 1 not in loaded.guilds
        # After materialization, the guild owns its own copies of the descriptions.
        await db_config.ensure_guild_categories(1)
        loaded = await db_config.load_config_from_db()
        assert 1 in loaded.guilds
        assert len(loaded.guild_descriptions[1]) == len(descriptions)
