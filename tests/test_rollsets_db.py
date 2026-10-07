"""Tests for the matchrolls admin data layer (cogs/matchrolls/db_config.py)."""

from db import models

from cogs.matchrolls import db_config
from cogs.matchrolls.constants import DEFAULT_GUILD_ID


async def _seed(rolls_config, descriptions):
    await db_config.seed_db_from_config(rolls_config, descriptions)


async def _item(guild_id: int, name: str, category: str):
    """The guild's item row, which the description services address by id."""
    return await models.RollItem.get(category__guild__guild_id=guild_id,
                                     category__name=category, name=name)


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
        alpha = await _item(999999, "Alpha", "map")
        delta = await _item(999999, "Delta", "landmark")
        assert len(await db_config.description_variants(999999, alpha.id)) == 1
        assert len(await db_config.description_variants(999999, delta.id)) == 1


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
        zeta = await _item(42424, "Zeta", "map")
        assert await db_config.add_description(
            42424, zeta.id, {"description": "Zeta flavor."}) is True
        await db_config.update_category(42424, "map", ["Eta"])
        # Inactive items keep their variants...
        assert len(await db_config.description_variants(42424, zeta.id)) == 1
        # ...and re-adding the name serves them again.
        await db_config.update_category(42424, "map", ["Zeta", "Eta"])
        variants = await db_config.description_variants(42424, zeta.id)
        assert [variant.description for variant in variants] == ["Zeta flavor."]

    async def test_delete_category_cascades(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        zeta = await _item(42424, "Zeta", "map")
        assert await db_config.add_description(
            42424, zeta.id, {"description": "Zeta flavor."}) is True
        assert await db_config.delete_category(42424, "map") is True
        assert await models.RollCategory.filter(
            guild__guild_id=42424, name="map").count() == 0
        assert await models.RollItem.filter(
            category__guild__guild_id=42424,
            category__name="map").count() == 0
        assert await db_config.description_variants(42424, zeta.id) == []


class TestDescriptionAdmin:
    async def test_add_description_requires_an_active_item(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        zeta = await _item(42424, "Zeta", "map")
        assert await db_config.add_description(
            42424, zeta.id, {"description": "Zeta flavor."}) is True
        # An id that is not one of the guild's items is refused.
        assert await db_config.add_description(
            42424, 999999, {"description": "x"}) is False
        # An item dropped from the set no longer accepts new variants.
        await db_config.update_category(42424, "map", ["Eta"])
        assert await db_config.add_description(
            42424, zeta.id, {"description": "x"}) is False

    async def test_variants_keep_their_order_and_fields(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        zeta = await _item(42424, "Zeta", "map")
        await db_config.add_description(
            42424, zeta.id, {"description": "First.", "color": 111,
                             "image_url": "https://example.com/one.png"})
        await db_config.add_description(
            42424, zeta.id, {"description": "Second."})
        embeds = await db_config.description_variant_embeds(42424, zeta.id)
        assert embeds == [
            {"title": "Zeta", "category": "Map", "description": "First.",
             "color": 111, "image": {"url": "https://example.com/one.png"}},
            {"title": "Zeta", "category": "Map", "description": "Second."}]

    async def test_update_description_targets_one_variant(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        zeta = await _item(42424, "Zeta", "map")
        await db_config.add_description(42424, zeta.id, {"description": "One."})
        await db_config.add_description(42424, zeta.id, {"description": "Two."})
        assert await db_config.update_description(
            42424, zeta.id, 2, {"description": "Changed.", "color": None}) is True
        variants = await db_config.description_variants(42424, zeta.id)
        assert [variant.description for variant in variants] == [
            "One.", "Changed."]
        assert variants[1].color is None

    async def test_update_and_remove_check_the_variant_index(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        zeta = await _item(42424, "Zeta", "map")
        await db_config.add_description(42424, zeta.id, {"description": "One."})
        assert await db_config.update_description(
            42424, zeta.id, 2, {"description": "x"}) is False
        assert await db_config.delete_description(42424, zeta.id, 0) is False
        assert await db_config.delete_description(42424, zeta.id, 1) is True
        assert await db_config.description_variants(42424, zeta.id) == []

    async def test_item_variant_counts(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        zeta = await _item(42424, "Zeta", "map")
        eta = await _item(42424, "Eta", "map")
        delta = await _item(42424, "Delta", "landmark")
        epsilon = await _item(42424, "Epsilon", "landmark")
        await db_config.add_description(42424, zeta.id, {"description": "One."})
        await db_config.add_description(42424, zeta.id, {"description": "Two."})
        rows = await db_config.item_variant_counts(42424)
        assert rows == [(zeta.id, "Zeta", 2, True), (eta.id, "Eta", 0, True),
                        (delta.id, "Delta", 0, True),
                        (epsilon.id, "Epsilon", 0, True)]
        # The category filter narrows it down.
        assert await db_config.item_variant_counts(42424, "landmark") == [
            (delta.id, "Delta", 0, True), (epsilon.id, "Epsilon", 0, True)]

    async def test_active_items_are_the_defaults_before_materializing(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        # A guild with no rows of its own rolls the [DEFAULT] items.
        items = await db_config.active_items(999999)
        assert [item.name for item in items] == [
            "Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
        assert [db_config.item_label(item) for item in items] == [
            "map — Alpha", "map — Beta", "map — Gamma",
            "landmark — Delta", "landmark — Epsilon"]

    async def test_defaults_keep_working_for_guilds_without_their_own_rows(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        # GuildB's inherited landmark is served from [DEFAULT].
        assert (await db_config.effective_category_sets(42424))["landmark"] == (
            "Delta, Epsilon")
        assert (await db_config.effective_category_sets(1))["map"] == (
            "Alpha, Beta, Gamma")
        assert DEFAULT_GUILD_ID == 0


class TestItemOptions:
    """An item option carries the row id, or the label the picker showed."""

    async def test_it_accepts_an_id_a_label_and_a_name(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        # A guild with no rows of its own resolves against the [DEFAULT] ones.
        alpha = await _item(DEFAULT_GUILD_ID, "Alpha", "map")
        for received in (str(alpha.id), "map — Alpha", "Alpha"):
            resolved = await db_config.item_for_option(999999, received)
            assert resolved is not None and resolved.id == alpha.id, received
        assert await db_config.item_for_option(999999, "Nope") is None

    async def test_another_guilds_id_is_refused(self, db, rolls_config,
                                                descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(42424)
        zeta = await _item(42424, "Zeta", "map")
        # 999999 sees the [DEFAULT] items, never GuildB's own rows.
        assert await db_config.item_for_option(999999, str(zeta.id)) is None


class TestTwoItemsOneName:
    """The same name in two categories is two items with their own
    descriptions: a name on its own never identifies an item."""

    async def test_each_item_keeps_its_own_variants(self, db, rolls_config,
                                                    descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(42424)
        # The fixture rolls Zeta under map; add the same name to landmark.
        await db_config.update_category(42424, "landmark", ["Delta", "Zeta"])
        map_zeta = await _item(42424, "Zeta", "map")
        landmark_zeta = await _item(42424, "Zeta", "landmark")
        assert map_zeta.id != landmark_zeta.id
        assert await db_config.add_description(
            42424, map_zeta.id, {"description": "Map only."}) is True
        assert [variant.description for variant in
                await db_config.description_variants(42424, map_zeta.id)] == [
            "Map only."]
        assert await db_config.description_variants(
            42424, landmark_zeta.id) == []
        # The label tells them apart, and each id resolves to its own row.
        resolved = await db_config.item_for_option(42424, str(map_zeta.id))
        assert db_config.item_label(resolved) == "map — Zeta"
        resolved = await db_config.item_for_option(42424, "landmark — Zeta")
        assert resolved.id == landmark_zeta.id


class TestAdoptingACategory:
    """A category [DEFAULT] gained after a guild was materialized is copied in."""

    async def test_it_copies_the_category_with_its_variants(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        # A guild whose own copy predates one of [DEFAULT]'s categories.
        await db_config.ensure_guild_categories(42424)
        await db_config.delete_category(42424, "map")
        assert await db_config.adopt_category(42424, "map") is True
        assert (await db_config.effective_category_sets(42424))["map"] == (
            "Alpha, Beta, Gamma")
        # The description variants came with the item they belong to.
        alpha = await _item(42424, "Alpha", "map")
        assert len(await db_config.description_variants(42424, alpha.id)) == 1

    async def test_it_refuses_a_category_the_guild_has(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(42424)
        assert await db_config.adopt_category(42424, "map") is False

    async def test_it_refuses_a_category_default_does_not_have(self, db):
        assert await db_config.adopt_category(42424, "nope") is False

    async def test_the_default_guild_owns_everything(self, db, rolls_config,
                                                     descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.adopt_category(DEFAULT_GUILD_ID, "map") is False


class TestOwnedCategories:
    """The pages edit the rows a guild owns, addressed by their row id."""

    async def test_own_categories_is_empty_before_materializing(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        assert await db_config.own_categories(999999) == []

    async def test_own_categories_lists_the_guilds_rows(
            self, db, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(42424)
        categories = await db_config.own_categories(42424)
        # Its own map (Zeta, Eta), plus the inherited landmark it was given.
        assert [category.name for category in categories] == [
            "map", "landmark"]
        assert len({category.id for category in categories}) == 2

    async def test_category_by_id_is_owned_only(self, db, rolls_config,
                                                descriptions):
        await _seed(rolls_config, descriptions)
        await db_config.ensure_guild_categories(42424)
        own = await db_config.own_categories(42424)
        found = await db_config.category_by_id(42424, own[0].id)
        assert found is not None and found.name == "map"
        # [DEFAULT]'s rows, another guild's rows and unknown ids are refused.
        default_map = await models.RollCategory.get(
            guild__guild_id=DEFAULT_GUILD_ID, name="map")
        assert await db_config.category_by_id(42424, default_map.id) is None
        assert await db_config.category_by_id(999999, own[0].id) is None
        assert await db_config.category_by_id(42424, 999999) is None

