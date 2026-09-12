"""Tests for the matchrolls admin data layer (cogs/matchrolls/db_config.py)."""

from db import models

from cogs.matchrolls import db_config
from cogs.matchrolls.constants import DEFAULT_GUILD_ID


async def _seed(rolls_config, descriptions):
    await db_config.seed_db_from_config(rolls_config, descriptions)


class TestEnsureGuildCategories:
    async def test_materializes_defaults_for_unknown_guild(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(999999)
        sets = await db_config.effective_category_sets(999999)
        assert sets == {"map": "Alpha, Beta, Gamma",
                        "landmark": "Delta, Epsilon"}

    async def test_leaves_existing_guild_rows_untouched(
            self, db, rolls_config, descriptions):
        # The fixture's GuildB section has its own categories.
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(42424)
        sets = await db_config.effective_category_sets(42424)
        assert sets["map"] == "Zeta, Eta"
        assert sets["landmark"] == "Delta, Epsilon"

    async def test_is_a_no_op_once_materialized(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(999999)
        await db_config.ensure_guild_categories(999999)
        categories = await models.RollCategory.filter(
            guild__guild_id=999999).count()
        assert categories == 2

    async def test_copies_descriptions_alongside_items(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(999999)
        # Each materialized item carries the [DEFAULT] descriptions.
        assert len(await db_config.description_variants(999999, "Alpha")) == 1
        assert len(await db_config.description_variants(999999, "Delta")) == 1


class TestCategoryAdmin:
    async def test_add_category(self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.add_category(
            999999, "deck", ["Standard", "Exiles"]) is True
        sets = await db_config.effective_category_sets(999999)
        # The [DEFAULT] categories are materialized alongside the new one.
        assert sets["deck"] == "Standard, Exiles"
        assert sets["map"] == "Alpha, Beta, Gamma"

    async def test_add_category_rejects_duplicate(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.add_category(999999, "map", ["X"]) is False

    async def test_update_category_replaces_items(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.update_category(
            42424, "map", ["Zeta", "Eta", "Theta"]) is True
        assert (await db_config.effective_category_sets(42424))["map"] == (
            "Zeta, Eta, Theta")
        # Dropping names keeps their rows (inactive), so they can come back.
        assert await db_config.update_category(42424, "map", ["Zeta"]) is True
        active, inactive = await db_config.list_category_items(42424, "map")
        assert active == ["Zeta"]
        assert inactive == ["Eta", "Theta"]

    async def test_update_category_renames(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.update_category(
            42424, "map", new_name="lands") is True
        sets = await db_config.effective_category_sets(42424)
        assert "map" not in sets
        assert sets["lands"] == "Zeta, Eta"

    async def test_update_category_rejects_unknown_and_conflicts(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.update_category(42424, "nope", ["X"]) is False
        assert await db_config.update_category(
            42424, "map", new_name="landmark") is False

    async def test_removed_item_keeps_its_variants(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.add_description(
            42424, "Zeta", {"description": "Zeta flavor."}) is True
        await db_config.update_category(42424, "map", ["Eta"])
        # Inactive items keep their variants...
        assert len(await db_config.description_variants(42424, "Zeta")) == 1
        # ...and re-adding the name serves them again.
        await db_config.update_category(42424, "map", ["Zeta", "Eta"])
        variants = await db_config.description_variants(42424, "Zeta")
        assert [variant.description for variant in variants] == ["Zeta flavor."]

    async def test_delete_category_cascades(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.add_description(
            42424, "Zeta", {"description": "Zeta flavor."}) is True
        assert await db_config.delete_category(42424, "map") is True
        assert await models.RollCategory.filter(
            guild__guild_id=42424, name="map").count() == 0
        assert await models.RollItem.filter(
            category__guild__guild_id=42424,
            category__name="map").count() == 0
        assert await db_config.description_variants(42424, "Zeta") == []


class TestDescriptionAdmin:
    async def test_add_description_requires_an_active_item(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.add_description(
            42424, "Zeta", {"description": "Zeta flavor."}) is True
        assert await db_config.add_description(
            42424, "nope", {"description": "x"}) is False
        # A name dropped from the set no longer accepts new variants.
        await db_config.update_category(42424, "map", ["Eta"])
        assert await db_config.add_description(
            42424, "Zeta", {"description": "x"}) is False

    async def test_variants_keep_their_order_and_fields(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.add_description(
            42424, "Zeta", {"description": "First.", "color": 111,
                            "image_url": "https://example.com/one.png"})
        await db_config.add_description(
            42424, "Zeta", {"description": "Second."})
        embeds = await db_config.description_variant_embeds(42424, "Zeta")
        assert embeds == [
            {"title": "Zeta", "category": "Map", "description": "First.",
             "color": 111, "image": {"url": "https://example.com/one.png"}},
            {"title": "Zeta", "category": "Map", "description": "Second."}]

    async def test_update_description_targets_one_variant(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.add_description(42424, "Zeta", {"description": "One."})
        await db_config.add_description(42424, "Zeta", {"description": "Two."})
        assert await db_config.update_description(
            42424, "Zeta", 2, {"description": "Changed.", "color": None}) is True
        variants = await db_config.description_variants(42424, "Zeta")
        assert [variant.description for variant in variants] == [
            "One.", "Changed."]
        assert variants[1].color is None

    async def test_update_and_remove_check_the_variant_index(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.add_description(42424, "Zeta", {"description": "One."})
        assert await db_config.update_description(
            42424, "Zeta", 2, {"description": "x"}) is False
        assert await db_config.delete_description(42424, "Zeta", 0) is False
        assert await db_config.delete_description(42424, "Zeta", 1) is True
        assert await db_config.description_variants(42424, "Zeta") == []

    async def test_item_variant_counts(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.add_description(42424, "Zeta", {"description": "One."})
        await db_config.add_description(42424, "Zeta", {"description": "Two."})
        rows = await db_config.item_variant_counts(42424)
        assert rows == [("Zeta", 2, True), ("Eta", 0, True),
                        ("Delta", 0, True), ("Epsilon", 0, True)]
        # The category filter narrows it down.
        assert await db_config.item_variant_counts(42424, "landmark") == [
            ("Delta", 0, True), ("Epsilon", 0, True)]

    async def test_active_item_names_materializes_the_guild(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        names = await db_config.active_item_names(999999)
        assert names == ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]

    async def test_defaults_keep_working_for_guilds_without_their_own_rows(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        # GuildB's inherited landmark is served from [DEFAULT].
        assert (await db_config.effective_category_sets(42424))["landmark"] == (
            "Delta, Epsilon")
        assert (await db_config.effective_category_sets(1))["map"] == (
            "Alpha, Beta, Gamma")
        assert DEFAULT_GUILD_ID == 0