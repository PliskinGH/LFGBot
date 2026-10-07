"""Tests for the roll-set pages: categories, items and description variants.

Database tests run against TEST_DATABASE_URL, like the other db suites: they
skip when it is unset or the server is unreachable (see the ``db`` fixture).
"""

from db import config_log, models

from cogs.matchrolls import db_config as rolls_db_config
from cogs.matchrolls.constants import DEFAULT_GUILD_ID

from tests.conftest import be_operator, csrf_of

# The rolls fixture's GuildB; OTHER_GUILD_ID is a server it does not configure.
ROLLS_GUILD_ID = 42424
OTHER_GUILD_ID = 90401


async def _seed(rolls_config, descriptions):
    await rolls_db_config.seed_db_from_config(rolls_config, descriptions)


async def _category(guild_id: int, name: str):
    """The guild's own category row."""
    return await models.RollCategory.get_or_none(guild__guild_id=guild_id,
                                                 name=name)


async def _item(guild_id: int, category: str, name: str):
    """The item row when it exists, found through its category."""
    return await models.RollItem.get_or_none(
        category__guild__guild_id=guild_id, category__name=category, name=name)


async def _changes() -> list[models.ConfigChange]:
    """Every logged change, newest first."""
    return await models.ConfigChange.all().order_by("-id")


def _post(client, path: str, data: dict, follow_redirects: bool = False):
    """Post a roll-set form with the session's CSRF token, as the pages require."""
    return client.post(path, data=dict(data, csrf_token=csrf_of(client)),
                       follow_redirects=follow_redirects)


class TestNewCategory:
    """Adding a roll category, the first write a server may make."""

    async def test_it_adds_one_and_queues_a_change(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        response = _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/new",
                         {"name": "deck", "items": "Standard, Exiles"})
        assert response.status_code == 303, response.text
        sets = await rolls_db_config.effective_category_sets(ROLLS_GUILD_ID)
        assert sets["deck"] == "Standard, Exiles"
        change, = await _changes()
        assert (change.action, change.summary, change.source) == (
            "rollset.category.add", "deck", config_log.SOURCE_WEB)
        assert change.guild_id == ROLLS_GUILD_ID
        assert change.applied_at is None  # the bot still owes it

    async def test_it_materializes_the_servers_own_configuration(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        # Guild 90401 has no rows of its own: it inherits [DEFAULT]'s, and this
        # first write copies them under its own row.
        assert await rolls_db_config.own_categories(OTHER_GUILD_ID) == []
        login(client, guilds={OTHER_GUILD_ID: "Server A"})
        _post(client, f"/g/{OTHER_GUILD_ID}/rollsets/new",
              {"name": "deck", "items": "Standard"})
        own = [category.name for category in
               await rolls_db_config.own_categories(OTHER_GUILD_ID)]
        assert own == ["map", "landmark", "deck"]

    async def test_a_reserved_or_empty_name_is_refused(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        page = f"/g/{ROLLS_GUILD_ID}/rollsets/new"
        assert "cannot be `id`" in _post(client, page,
                                         {"name": "id", "items": "a"}).text
        assert "at least one item" in _post(client, page,
                                            {"name": "deck", "items": ""}).text
        assert await _category(ROLLS_GUILD_ID, "deck") is None
        assert await _changes() == []


class TestEditCategory:
    """One form renames a category and replaces its item set."""

    async def test_it_renames_and_replaces_the_items(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        response = _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}",
                         {"name": "terrain", "items": "Zeta"})
        assert response.status_code == 303, response.text
        sets = await rolls_db_config.effective_category_sets(ROLLS_GUILD_ID)
        assert "terrain" in sets and sets["terrain"] == "Zeta"
        assert "map" not in sets
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.category.update", "map -> terrain")

    async def test_it_shows_the_stored_name_and_items(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        page = client.get(f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}").text
        assert 'value="map"' in page
        assert "Zeta, Eta" in page
        # No context name may be read as a dict method (items, values, keys).
        assert "built-in method" not in page


class TestRemoveCategory:
    """Deleting a category: a confirmation page in, a deletion out."""

    async def test_it_removes_the_category_with_its_items(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        page = f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/remove"
        assert "This cannot be undone" in client.get(page).text
        response = _post(client, page, {})
        assert response.status_code == 303, response.text
        assert await _category(ROLLS_GUILD_ID, "map") is None
        assert await models.RollItem.filter(
            category__guild__guild_id=ROLLS_GUILD_ID,
            category__name="map").count() == 0
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.category.remove", "map")


class TestAdoptingACategory:
    """Customising an inherited category gives the server its own copy."""

    async def test_it_copies_the_category_and_opens_it(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        # Guild 90401 has no rows of its own: it inherits [DEFAULT]'s.
        login(client, guilds={OTHER_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{OTHER_GUILD_ID}/rollsets/adopt",
                         {"name": "map"})
        assert response.status_code == 303, response.text
        category = await _category(OTHER_GUILD_ID, "map")
        assert category is not None
        assert response.headers["location"].endswith(
            f"/rollsets/{category.id}")
        # The whole [DEFAULT] configuration came along, and it is logged.
        own = await rolls_db_config.own_categories(OTHER_GUILD_ID)
        assert [row.name for row in own] == ["map", "landmark"]
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.category.adopt", "map")

    async def test_a_name_nobody_has_materializes_nothing(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={OTHER_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{OTHER_GUILD_ID}/rollsets/adopt",
                         {"name": "nope"})
        assert response.status_code == 404
        assert await rolls_db_config.own_categories(OTHER_GUILD_ID) == []
        assert await _changes() == []


class TestItemVariants:
    """One item's description variants, added and changed from its page."""

    async def test_it_adds_a_variant_with_a_colour(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        response = _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                                f"/items/{item.id}/variants/new",
                         {"description": "A flavour.", "use_color": "1",
                          "color": "#ff0000", "image_url": "",
                          "thumbnail_url": ""})
        assert response.status_code == 303, response.text
        variants = await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id)
        assert [(v.description, v.color) for v in variants] == [
            ("A flavour.", 16711680)]
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.description.add", "map — Zeta")

    async def test_an_untouched_colour_box_stores_no_colour(
            self, db, client, login, rolls_config, descriptions):
        # A colour input always posts a value: the box is what makes it count,
        # and unticked the bot rolls a random colour for the variant.
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                      f"/items/{item.id}/variants/new",
              {"description": "A flavour.", "color": "#ffffff"})
        variants = await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id)
        assert [variant.color for variant in variants] == [None]
        assert "built-in method" not in client.get(
            f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
            f"/items/{item.id}").text

    async def test_it_edits_and_removes_a_variant(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        base = (f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                f"/items/{item.id}/variants")
        _post(client, f"{base}/new", {"description": "First."})
        variant, = await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id)
        # The id addresses the row; an emptied colour clears it.
        response = _post(client, f"{base}/{variant.id}",
                         {"description": "Changed.", "color": "#ffffff"})
        assert response.status_code == 303, response.text
        variants = await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id)
        assert [(v.description, v.color) for v in variants] == [
            ("Changed.", None)]
        # Removing asks first, then deletes.
        remove = f"{base}/{variant.id}/remove"
        assert "cannot be undone" in client.get(remove).text
        assert _post(client, remove, {}).status_code == 303
        assert await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id) == []
        assert (await _changes())[0].action == "rollset.description.remove"

    async def test_the_variant_forms_show_the_stored_values(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        base = (f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                f"/items/{item.id}/variants")
        blank = client.get(f"{base}/new").text
        assert "Add a description variant" in blank
        _post(client, f"{base}/new", {"description": "First.", "use_color": "1",
                                      "color": "#00ff00"})
        variant, = await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id)
        edit = client.get(f"{base}/{variant.id}").text
        assert "Edit description variant" in edit
        # The stored text and colour come back, with the box ticked.
        assert "First." in edit and "#00ff00" in edit and "checked" in edit
        for text in (blank, edit):
            assert "built-in method" not in text

    async def test_an_unusable_field_shows_the_form_again(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        long_url = "https://example.invalid/" + "x" * 2048
        response = _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                                f"/items/{item.id}/variants/new",
                         {"description": "Kept.", "use_color": "1",
                          "color": "not-a-colour", "image_url": long_url})
        assert response.status_code == 200, response.text
        assert "`image` must be a URL" in response.text
        assert "must be a colour" in response.text
        # The form comes back as it was posted, and nothing is written.
        assert "Kept." in response.text
        assert "built-in method" not in response.text
        assert await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id) == []
        assert await _changes() == []


class TestWhatThePagesRefuse:
    """Ids that are not this server's, missing tokens, another server's manager."""

    async def test_an_unknown_category_is_not_found(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        assert client.get(
            f"/g/{ROLLS_GUILD_ID}/rollsets/999999").status_code == 404
        assert client.get(
            f"/g/{ROLLS_GUILD_ID}/rollsets/999999/remove").status_code == 404

    async def test_another_servers_category_is_not_found(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        category = await _category(ROLLS_GUILD_ID, "map")
        login(client, guilds={OTHER_GUILD_ID: "Server A"})
        assert client.get(
            f"/g/{OTHER_GUILD_ID}/rollsets/{category.id}").status_code == 404

    async def test_an_item_of_another_category_is_not_found(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        other = await _category(ROLLS_GUILD_ID, "landmark")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        assert client.get(f"/g/{ROLLS_GUILD_ID}/rollsets/{other.id}"
                          f"/items/{item.id}").status_code == 404

    async def test_a_variant_id_that_is_not_the_items_is_not_found(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        assert client.get(
            f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/items/{item.id}"
            f"/variants/999999").status_code == 404

    async def test_a_write_without_the_session_token_is_refused(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        response = client.post(f"/g/{ROLLS_GUILD_ID}/rollsets/new",
                               data={"name": "deck", "items": "Standard"})
        assert response.status_code == 403
        assert await _category(ROLLS_GUILD_ID, "deck") is None

    async def test_a_manager_of_another_server_cannot_reach_them(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={OTHER_GUILD_ID: "Server A"})
        assert client.get(
            f"/g/{ROLLS_GUILD_ID}/rollsets/new").status_code == 404


class TestTheDefaultConfiguration:
    """The [DEFAULT] roll sets, which only the panel's operators may edit."""

    async def test_an_operator_edits_them(
            self, db, client, login, monkeypatch, rolls_config, descriptions):
        be_operator(monkeypatch)
        await _seed(rolls_config, descriptions)
        login(client)
        response = _post(client, "/ops/default/rollsets/new",
                         {"name": "deck", "items": "Standard"})
        assert response.status_code == 303, response.text
        assert await _category(DEFAULT_GUILD_ID, "deck") is not None
        change, = await _changes()
        assert change.guild_id == DEFAULT_GUILD_ID

    async def test_a_manager_may_not(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        assert client.get("/ops/default/rollsets/new").status_code in (403, 404)

    async def test_the_default_configuration_offers_no_adopt(
            self, db, client, login, monkeypatch, rolls_config, descriptions):
        # It owns everything, so there is nothing to copy in.
        be_operator(monkeypatch)
        await _seed(rolls_config, descriptions)
        login(client)
        response = _post(client, "/ops/default/rollsets/adopt",
                         {"name": "map"})
        assert response.status_code == 404


class TestItemPages:
    """Adding, renaming and removing one item, from the category and item pages."""

    async def test_the_category_page_offers_adding_an_item(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        page = client.get(f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}").text
        assert f"/rollsets/{category.id}/items/new" in page
        assert "built-in method" not in page

    async def test_it_adds_an_item_with_its_first_variant(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        response = _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                                "/items/new",
                         {"name": "Theta", "description": "A flavour.",
                          "use_color": "1", "color": "#ff0000"})
        assert response.status_code == 303, response.text
        # It lands on the category's page, where the new item is listed.
        assert response.headers["location"].endswith(
            f"/rollsets/{category.id}")
        item = await _item(ROLLS_GUILD_ID, "map", "Theta")
        variants = await rolls_db_config.description_variants(
            ROLLS_GUILD_ID, item.id)
        assert [(v.description, v.color) for v in variants] == [
            ("A flavour.", 16711680)]
        # The set follows the rows: the new item is rolled straight away.
        assert (await rolls_db_config.effective_category_sets(
            ROLLS_GUILD_ID))["map"] == "Zeta, Eta, Theta"
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.item.add", "map — Theta")

    async def test_a_blank_flavour_is_allowed_and_stays_a_blank_variant(
            self, db, client, login, rolls_config, descriptions):
        # A variant form requires something; an item may be added without a
        # flavour at all, and rolls a plain embed.
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        response = _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                                "/items/new",
                         {"name": "Theta", "description": ""})
        assert response.status_code == 303, response.text
        item = await _item(ROLLS_GUILD_ID, "map", "Theta")
        assert [(v.description, v.color, v.image_url) for v in
                await rolls_db_config.description_variants(
                    ROLLS_GUILD_ID, item.id)] == [("", None, None)]

    async def test_a_bad_name_is_refused_and_a_taken_one_reported(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        page = f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/items/new"
        assert "`name` must be 1-50 characters" in _post(
            client, page, {"name": "", "description": ""}).text
        assert "is already an item of `map`" in _post(
            client, page, {"name": "Zeta", "description": ""}).text
        assert await _changes() == []

    async def test_a_new_name_in_the_set_is_given_a_variant(
            self, db, client, login, rolls_config, descriptions):
        # The category form's item set is the other way in: a name the category
        # has never had must arrive rollable too.
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}",
              {"name": "map", "items": "Zeta, Eta, Theta"})
        item = await _item(ROLLS_GUILD_ID, "map", "Theta")
        assert [v.description for v in
                await rolls_db_config.description_variants(
                    ROLLS_GUILD_ID, item.id)] == [""]

    async def test_it_renames_an_item(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        base = f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/items/{item.id}"
        assert 'value="Zeta"' in client.get(f"{base}/rename").text
        response = _post(client, f"{base}/rename", {"name": "Omega"})
        assert response.status_code == 303, response.text
        assert await _item(ROLLS_GUILD_ID, "map", "Omega") is not None
        # The row keeps its id, so the item's page follows the new name.
        assert "Omega" in client.get(base).text
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.item.rename", "map — Zeta -> Omega")

    async def test_a_rename_onto_another_item_is_refused(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        response = _post(client,
                         f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                         f"/items/{item.id}/rename", {"name": "Eta"})
        assert "`Eta` is already an item of `map`" in response.text
        assert await _item(ROLLS_GUILD_ID, "map", "Zeta") is not None
        assert await _changes() == []

    async def test_it_takes_an_item_out_of_the_set(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        await rolls_db_config.add_description(ROLLS_GUILD_ID, item.id,
                                              {"description": "Kept."})
        base = f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/items/{item.id}"
        response = _post(client, f"{base}/remove", {})
        assert response.status_code == 303, response.text
        # It lands on the category's page, where the item stays listed as off.
        assert response.headers["location"].endswith(
            f"/rollsets/{category.id}")
        # The row stays, switched off with its variant, and leaves the set.
        assert (await _item(ROLLS_GUILD_ID, "map", "Zeta")).active is False
        assert [v.description for v in
                await rolls_db_config.description_variants(
                    ROLLS_GUILD_ID, item.id)] == ["Kept."]
        assert (await rolls_db_config.effective_category_sets(
            ROLLS_GUILD_ID))["map"] == "Eta"
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.item.remove", "map — Zeta")
        # Its page says so, and no longer offers the button.
        page = client.get(base).text
        assert "Not rolled" in page
        assert f"/items/{item.id}/remove" not in page
        # Adding the name back through the set restores the item.
        _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}",
              {"name": "map", "items": "Zeta, Eta"})
        assert (await _item(ROLLS_GUILD_ID, "map", "Zeta")).active is True
        assert [v.description for v in
                await rolls_db_config.description_variants(
                    ROLLS_GUILD_ID, item.id)] == ["Kept."]

    async def test_the_item_pages_refuse_stale_ids_and_missing_tokens(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        assert client.get(f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                          "/items/999999/rename").status_code == 404
        assert client.get(f"/g/{ROLLS_GUILD_ID}/rollsets/999999"
                          "/items/new").status_code == 404
        response = client.post(
            f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/items/new",
            data={"name": "Theta", "description": ""})
        assert response.status_code == 403
        assert await _item(ROLLS_GUILD_ID, "map", "Theta") is None

    async def test_the_buttons_follow_the_items_state(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        base = f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/items/{item.id}"
        active = client.get(base).text
        assert "Rename" in active and "Add a variant" in active
        assert "Remove from the set" in active
        assert "Add back" not in active and "Delete permanently" not in active
        # Removed: the ways back in and the permanent way out replace it.
        await rolls_db_config.remove_item(ROLLS_GUILD_ID, item.id)
        removed = client.get(base).text
        assert "Add back" in removed and "Delete permanently" in removed
        assert "Remove from the set" not in removed
        assert "built-in method" not in removed

    async def test_the_category_table_offers_the_item_actions(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        active = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        removed = await _item(ROLLS_GUILD_ID, "map", "Eta")
        await rolls_db_config.remove_item(ROLLS_GUILD_ID, removed.id)
        page = client.get(f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}").text
        # The name is plain text: the way in is a button of its own.
        assert f"<td>{active.name}</td>" in page
        assert "Edit variants" in page
        assert f"/items/{active.id}/rename" in page
        assert f"/items/{active.id}/remove" in page
        assert f"/items/{active.id}/restore" not in page
        # A removed item comes back, or goes for good.
        assert f"/items/{removed.id}/restore" in page
        assert f"/items/{removed.id}/delete" in page
        assert f"/items/{removed.id}/remove" not in page
        assert "built-in method" not in page

    async def test_add_back_puts_the_item_in_the_set_again(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        await rolls_db_config.add_description(ROLLS_GUILD_ID, item.id,
                                              {"description": "Kept."})
        await rolls_db_config.remove_item(ROLLS_GUILD_ID, item.id)
        response = _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                                f"/items/{item.id}/restore", {})
        assert response.status_code == 303, response.text
        assert response.headers["location"].endswith(
            f"/rollsets/{category.id}")
        assert (await _item(ROLLS_GUILD_ID, "map", "Zeta")).active is True
        assert [v.description for v in
                await rolls_db_config.description_variants(
                    ROLLS_GUILD_ID, item.id)] == ["Kept."]
        assert (await rolls_db_config.effective_category_sets(
            ROLLS_GUILD_ID))["map"] == "Zeta, Eta"
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.item.add", "map — Zeta")

    async def test_delete_permanently_asks_then_removes_it(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        await rolls_db_config.add_description(ROLLS_GUILD_ID, item.id,
                                              {"description": "Gone."})
        await rolls_db_config.remove_item(ROLLS_GUILD_ID, item.id)
        url = (f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
               f"/items/{item.id}/delete")
        confirm = client.get(url).text
        assert "cannot be undone" in confirm
        assert "1 description variant(s)" in confirm
        assert ">Delete</button>" in confirm
        # The warning is about this deletion only: the set-removal story does
        # not apply to it.
        assert "removed from this configuration" in confirm
        assert "keeps them" not in confirm
        # Cancel goes back to the page whose button opened the confirmation.
        assert f'/rollsets/{category.id}"' in confirm
        assert f'items/{item.id}"' in client.get(f"{url}?from=item").text
        response = _post(client, url, {})
        assert response.status_code == 303, response.text
        assert response.headers["location"].endswith(
            f"/rollsets/{category.id}")
        assert await _item(ROLLS_GUILD_ID, "map", "Zeta") is None
        assert await models.RollDescription.filter(item_id=item.id).count() == 0
        assert (await rolls_db_config.effective_category_sets(
            ROLLS_GUILD_ID))["map"] == "Eta"
        change, = await _changes()
        assert (change.action, change.summary) == (
            "rollset.item.delete", "map — Zeta")

    async def test_the_item_writes_need_the_token_and_the_servers_item(
            self, db, client, login, rolls_config, descriptions):
        await _seed(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        category = await _category(ROLLS_GUILD_ID, "map")
        item = await _item(ROLLS_GUILD_ID, "map", "Zeta")
        base = f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}/items/{item.id}"
        await rolls_db_config.remove_item(ROLLS_GUILD_ID, item.id)
        for path in (f"{base}/restore", f"{base}/delete"):
            response = client.post(path, data={"no": "token"})
            assert response.status_code == 403, path
        assert (await _item(ROLLS_GUILD_ID, "map", "Zeta")) is not None
        # An id that is not the category's is not found.
        assert client.get(f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                          "/items/999999/delete").status_code == 404
        assert _post(client, f"/g/{ROLLS_GUILD_ID}/rollsets/{category.id}"
                             "/items/999999/restore", {}).status_code == 404
