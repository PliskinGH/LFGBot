"""Tests for the /rollsets admin commands (cogs/matchrolls/admin.py)."""

import configparser
from types import SimpleNamespace

import pytest

from cogs.matchrolls import MatchRolls, db_config
from cogs.matchrolls.db_config import LoadedRollsConfig
from common import constants as common_constants
from common.views import ConfirmView

from tests.conftest import FakeBot, FakeInteraction, FakeMember


async def _noop(*args, **kwargs):
    return None


async def _sets(guild_id):
    return {"map": "Zeta", "landmark": "Delta"}


async def _loaded_config() -> LoadedRollsConfig:
    loaded = LoadedRollsConfig()
    loaded.default_categories = {"map": "Alpha, Beta"}
    loaded.guilds[42424] = {"map": "Zeta", "landmark": "Delta"}
    loaded.default_descriptions = [{"title": "Alpha", "category": "Map"}]
    return loaded


def _config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read_string("[DEFAULT]\nmap = Alpha, Beta\nlandmark = Delta\n")
    return config


@pytest.fixture
def rollset_cog(monkeypatch):
    """A MatchRolls cog whose database calls are stubbed out."""
    bot = FakeBot()
    bot.db = SimpleNamespace()
    monkeypatch.setattr(db_config, "load_config_from_db", _loaded_config)
    monkeypatch.setattr(db_config, "ensure_guild_categories", _noop)
    monkeypatch.setattr(db_config, "effective_category_sets", _sets)
    return MatchRolls(bot=bot, config=_config(), descriptions=[])


def _manager(user_id=1) -> FakeMember:
    member = FakeMember(user_id, "Manager")
    member.guild_permissions = SimpleNamespace(manage_guild=True)
    return member


def _interaction(user=None, guild_id=42424) -> FakeInteraction:
    return FakeInteraction(user=user or _manager(), guild_id=guild_id)


class TestGuards:
    @pytest.mark.asyncio
    async def test_requires_manage_guild(self, rollset_cog):
        interaction = _interaction(user=FakeMember(2, "Member"))
        await MatchRolls.rollsets_list.callback(rollset_cog, interaction)
        assert "Only server managers" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_config_file_mode_is_read_only(self, monkeypatch):
        bot = FakeBot()
        monkeypatch.setattr(db_config, "load_config_from_db", _loaded_config)
        cog = MatchRolls(bot=bot, config=_config(), descriptions=[])
        interaction = _interaction()
        await MatchRolls.rollsets_list.callback(cog, interaction)
        assert "config-file mode" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_outside_a_guild(self, rollset_cog):
        interaction = _interaction()
        interaction.guild_id = None
        await MatchRolls.rollsets_list.callback(rollset_cog, interaction)
        assert "only available in a server" in interaction.response.messages[0][0]


class TestRollsetsList:
    @pytest.mark.asyncio
    async def test_lists_categories_and_item_counts(self, rollset_cog):
        interaction = _interaction()
        await MatchRolls.rollsets_list.callback(rollset_cog, interaction)
        content = interaction.response.messages[0][0]
        assert "# Roll sets" in content
        assert "`map` — Zeta" in content
        assert "`landmark` — Delta" in content

    @pytest.mark.asyncio
    async def test_no_categories(self, rollset_cog, monkeypatch):
        async def empty(guild_id):
            return {}
        monkeypatch.setattr(db_config, "effective_category_sets", empty)
        interaction = _interaction()
        await MatchRolls.rollsets_list.callback(rollset_cog, interaction)
        assert "No roll categories are configured." in (
            interaction.response.messages[0][0])


class TestRollsetsShow:
    @pytest.mark.asyncio
    async def test_lists_items_with_removed_note(self, rollset_cog, monkeypatch):
        async def items(guild_id, category):
            return ["Zeta", "Eta"], ["Theta"]
        monkeypatch.setattr(db_config, "list_category_items", items)
        interaction = _interaction()
        await MatchRolls.rollsets_show.callback(rollset_cog, interaction, "map")
        content = interaction.response.messages[0][0]
        assert "# Category: `map`" in content
        assert "1. Zeta" in content
        assert "2. Eta" in content
        assert "Theta" in content

    @pytest.mark.asyncio
    async def test_unknown_category(self, rollset_cog, monkeypatch):
        async def items(guild_id, category):
            return [], []
        monkeypatch.setattr(db_config, "list_category_items", items)
        interaction = _interaction()
        await MatchRolls.rollsets_show.callback(rollset_cog, interaction, "nope")
        assert "no roll category" in interaction.response.messages[0][0].lower()


class TestRollsetsAdd:
    @pytest.mark.asyncio
    async def test_adds_category(self, rollset_cog, monkeypatch):
        calls = {}

        async def add(guild_id, name, items):
            calls["args"] = (guild_id, name, items)
            return True
        monkeypatch.setattr(db_config, "add_category", add)
        interaction = _interaction()
        await MatchRolls.rollsets_add.callback(
            rollset_cog, interaction, "deck", "Standard, Exiles")
        assert calls["args"] == (42424, "deck", ["Standard", "Exiles"])
        assert "`deck` added (2 items)" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_duplicate_category(self, rollset_cog, monkeypatch):
        monkeypatch.setattr(db_config, "add_category", _noop_return(False))
        interaction = _interaction()
        await MatchRolls.rollsets_add.callback(
            rollset_cog, interaction, "map", "Zeta")
        assert "already exists" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_bad_names(self, rollset_cog):
        interaction = _interaction()
        await MatchRolls.rollsets_add.callback(rollset_cog, interaction, " ", "X")
        assert "`category` must be" in interaction.response.messages[0][0]

        interaction = _interaction()
        await MatchRolls.rollsets_add.callback(
            rollset_cog, interaction, "deck", "A, A")
        assert "duplicate" in interaction.response.messages[0][0]

        interaction = _interaction()
        await MatchRolls.rollsets_add.callback(rollset_cog, interaction, "deck", " ")
        assert "`items`" in interaction.response.messages[0][0]


class TestRollsetsUpdate:
    @pytest.mark.asyncio
    async def test_replaces_items(self, rollset_cog, monkeypatch):
        calls = {}

        async def update(guild_id, name, item_names, new_name):
            calls["args"] = (guild_id, name, item_names, new_name)
            return True
        monkeypatch.setattr(db_config, "update_category", update)
        interaction = _interaction()
        await MatchRolls.rollsets_update.callback(
            rollset_cog, interaction, "map", "Zeta, Eta")
        assert calls["args"] == (42424, "map", ["Zeta", "Eta"], None)
        assert "`map` updated (2 items)" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_renames(self, rollset_cog, monkeypatch):
        async def update(guild_id, name, item_names, new_name):
            return True
        monkeypatch.setattr(db_config, "update_category", update)
        interaction = _interaction()
        await MatchRolls.rollsets_update.callback(
            rollset_cog, interaction, "map", None, "lands")
        assert "renamed to `lands`" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_unknown_and_conflicting_names(
            self, rollset_cog, monkeypatch):
        interaction = _interaction()
        await MatchRolls.rollsets_update.callback(
            rollset_cog, interaction, "map", None, "landmark")
        assert "already a roll category" in interaction.response.messages[0][0]

        async def update(guild_id, name, item_names, new_name):
            return False
        monkeypatch.setattr(db_config, "update_category", update)
        interaction = _interaction()
        await MatchRolls.rollsets_update.callback(
            rollset_cog, interaction, "nope", "X")
        assert "no roll category `nope`" in interaction.response.messages[0][0]


def _noop_return(value):
    async def call(*args, **kwargs):
        return value
    return call


class TestRollsetsRemove:
    @pytest.mark.asyncio
    async def test_warns_before_deleting_and_confirms(
            self, rollset_cog, monkeypatch):
        deletes = []

        async def items(guild_id, category):
            return ["Zeta", "Eta"], ["Theta"]

        async def counts(guild_id, category):
            return [("Zeta", 2, True), ("Eta", 0, True), ("Theta", 0, False)]

        async def delete(guild_id, category):
            deletes.append((guild_id, category))
            return True

        monkeypatch.setattr(db_config, "list_category_items", items)
        monkeypatch.setattr(db_config, "item_variant_counts", counts)
        monkeypatch.setattr(db_config, "delete_category", delete)

        interaction = _interaction()
        await MatchRolls.rollsets_remove.callback(
            rollset_cog, interaction, "map")
        content, _, ephemeral, view = interaction.response.messages[0]
        assert "This will permanently delete" in content
        assert "**3 item(s)**" in content
        assert "**2 description variant(s)**" in content
        assert ephemeral is True
        assert isinstance(view, ConfirmView)

        # Confirming deletes and reports; the view is removed.
        click = _interaction()
        await view._confirm_callback(click)
        assert deletes == [(42424, "map")]
        assert click.response.edited["content"] == "Deleted roll category `map`."
        assert click.response.edited["view"] is None

    @pytest.mark.asyncio
    async def test_cancel_keeps_the_category(self, rollset_cog, monkeypatch):
        async def items(guild_id, category):
            return ["Zeta"], []

        async def counts(guild_id, category):
            return [("Zeta", 1, True)]

        monkeypatch.setattr(db_config, "list_category_items", items)
        monkeypatch.setattr(db_config, "item_variant_counts", counts)
        monkeypatch.setattr(db_config, "delete_category", _noop_return(False))

        interaction = _interaction()
        await MatchRolls.rollsets_remove.callback(
            rollset_cog, interaction, "map")
        view = interaction.response.messages[0][3]
        click = _interaction()
        await view._cancel_callback(click)
        assert click.response.edited["content"] == "Cancelled."

    @pytest.mark.asyncio
    async def test_only_the_requester_can_answer(self, rollset_cog, monkeypatch):
        async def items(guild_id, category):
            return ["Zeta"], []

        async def counts(guild_id, category):
            return [("Zeta", 0, True)]

        monkeypatch.setattr(db_config, "list_category_items", items)
        monkeypatch.setattr(db_config, "item_variant_counts", counts)

        interaction = _interaction()
        await MatchRolls.rollsets_remove.callback(
            rollset_cog, interaction, "map")
        view = interaction.response.messages[0][3]
        other = _interaction(user=_manager(2))
        await view._confirm_callback(other)
        assert "Only the requesting user" in other.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_unknown_category(self, rollset_cog, monkeypatch):
        async def items(guild_id, category):
            return [], []
        monkeypatch.setattr(db_config, "list_category_items", items)
        interaction = _interaction()
        await MatchRolls.rollsets_remove.callback(
            rollset_cog, interaction, "nope")
        assert "no roll category" in interaction.response.messages[0][0].lower()


class TestDescriptionCommands:
    @pytest.mark.asyncio
    async def test_list_shows_variant_counts(self, rollset_cog, monkeypatch):
        async def counts(guild_id, category=None):
            return [("Zeta", 2, True), ("Eta", 0, True)]
        monkeypatch.setattr(db_config, "item_variant_counts", counts)
        interaction = _interaction()
        await MatchRolls.description_list.callback(rollset_cog, interaction)
        content = interaction.response.messages[0][0]
        assert "`Zeta` — 2 variant(s)." in content
        assert "`Eta` — 0 variant(s)." in content

    @pytest.mark.asyncio
    async def test_show_all_or_one_variant(self, rollset_cog, monkeypatch):
        async def embeds(guild_id, item):
            return [{"title": "Zeta", "category": "Map", "description": "One."},
                    {"title": "Zeta", "category": "Map", "description": "Two."}]
        monkeypatch.setattr(db_config, "description_variant_embeds", embeds)

        interaction = _interaction()
        await MatchRolls.description_show.callback(rollset_cog, interaction, "Zeta")
        assert len(interaction.response.messages[0][1]) == 2

        interaction = _interaction()
        await MatchRolls.description_show.callback(
            rollset_cog, interaction, "Zeta", 2)
        embeds_sent = interaction.response.messages[0][1]
        assert len(embeds_sent) == 1
        assert embeds_sent[0].description == "Two."

    @pytest.mark.asyncio
    async def test_show_bounds_and_unknown_item(self, rollset_cog, monkeypatch):
        async def embeds(guild_id, item):
            return [{"title": "Zeta", "category": "Map"}]
        monkeypatch.setattr(db_config, "description_variant_embeds", embeds)
        interaction = _interaction()
        await MatchRolls.description_show.callback(
            rollset_cog, interaction, "Zeta", 3)
        assert "no variant #3" in interaction.response.messages[0][0]

        async def none(guild_id, item):
            return []
        monkeypatch.setattr(db_config, "description_variant_embeds", none)
        interaction = _interaction()
        await MatchRolls.description_show.callback(rollset_cog, interaction, "Nope")
        assert "no description variants" in interaction.response.messages[0][0]


class TestDescriptionMutations:
    @pytest.mark.asyncio
    async def test_add_passes_the_parsed_fields(self, rollset_cog, monkeypatch):
        calls = {}

        async def add(guild_id, item, fields):
            calls["args"] = (guild_id, item, fields)
            return True
        monkeypatch.setattr(db_config, "add_description", add)
        interaction = _interaction()
        await MatchRolls.description_add.callback(
            rollset_cog, interaction, "Zeta", "Flavor.", "111", "https://a/b.png")
        assert calls["args"] == (42424, "Zeta",
                                 {"description": "Flavor.", "color": 111,
                                  "image_url": "https://a/b.png"})
        assert "Added a description variant to `Zeta`" in (
            interaction.response.messages[0][0])

        interaction = _interaction()
        await MatchRolls.description_add.callback(
            rollset_cog, interaction, "Zeta", common_constants.RESET_SENTINEL, None)
        # "-" clears the text to an empty description (same as update).
        assert calls["args"] == (42424, "Zeta", {"description": ""})

    @pytest.mark.asyncio
    async def test_add_validates_text_color_and_item(
            self, rollset_cog, monkeypatch):
        interaction = _interaction()
        await MatchRolls.description_add.callback(
            rollset_cog, interaction, "Zeta", "", None)
        assert "`text` must be" in interaction.response.messages[0][0]

        interaction = _interaction()
        await MatchRolls.description_add.callback(
            rollset_cog, interaction, "Zeta", "Flavor.", "nope")
        assert "`color` must be" in interaction.response.messages[0][0]

        monkeypatch.setattr(db_config, "add_description", _noop_return(False))
        interaction = _interaction()
        await MatchRolls.description_add.callback(
            rollset_cog, interaction, "Nope", "Flavor.")
        assert "no active item" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_update_requires_a_variant_and_only_sends_given_fields(
            self, rollset_cog, monkeypatch):
        calls = {}

        async def update(guild_id, item, variant, fields):
            calls["args"] = (guild_id, item, variant, fields)
            return True
        monkeypatch.setattr(db_config, "update_description", update)
        interaction = _interaction()
        await MatchRolls.description_update.callback(
            rollset_cog, interaction, "Zeta", 2, "New text.", common_constants.RESET_SENTINEL)
        # "-" clears the colour; omitted image/thumbnail are left untouched.
        assert calls["args"] == (42424, "Zeta", 2,
                                 {"description": "New text.", "color": None})
        assert "Updated variant #2 of `Zeta`" in (
            interaction.response.messages[0][0])

    @pytest.mark.asyncio
    async def test_update_dash_clears_text(self, rollset_cog, monkeypatch):
        calls = {}

        async def update(guild_id, item, variant, fields):
            calls["args"] = (guild_id, item, variant, fields)
            return True
        monkeypatch.setattr(db_config, "update_description", update)
        interaction = _interaction()
        await MatchRolls.description_update.callback(
            rollset_cog, interaction, "Zeta", 1, common_constants.RESET_SENTINEL)
        # "-" clears the description text to an empty string.
        assert calls["args"] == (42424, "Zeta", 1, {"description": ""})
        assert "Updated variant #1 of `Zeta`" in (
            interaction.response.messages[0][0])

    @pytest.mark.asyncio
    async def test_update_bounds_and_missing_variant(
            self, rollset_cog, monkeypatch):
        interaction = _interaction()
        await MatchRolls.description_update.callback(
            rollset_cog, interaction, "Zeta", 0, "Text.")
        assert "`variant` must be a positive index" in (
            interaction.response.messages[0][0])

        monkeypatch.setattr(db_config, "update_description", _noop_return(False))
        interaction = _interaction()
        await MatchRolls.description_update.callback(
            rollset_cog, interaction, "Zeta", 5, "Text.")
        assert "no variant #5" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_remove_requires_a_variant(self, rollset_cog, monkeypatch):
        calls = []

        async def delete(guild_id, item, variant):
            calls.append((guild_id, item, variant))
            return True
        monkeypatch.setattr(db_config, "delete_description", delete)
        interaction = _interaction()
        await MatchRolls.description_remove.callback(
            rollset_cog, interaction, "Zeta", 1)
        assert calls == [(42424, "Zeta", 1)]
        assert "Removed variant #1 from `Zeta`" in (
            interaction.response.messages[0][0])

        monkeypatch.setattr(db_config, "delete_description", _noop_return(False))
        interaction = _interaction()
        await MatchRolls.description_remove.callback(
            rollset_cog, interaction, "Zeta", 9)
        assert "no variant #9" in interaction.response.messages[0][0]


class TestAutocomplete:
    @pytest.mark.asyncio
    async def test_categories_and_items(self, rollset_cog, monkeypatch):
        async def items(guild_id):
            return ["Zeta", "Eta"]
        monkeypatch.setattr(db_config, "active_item_names", items)

        interaction = _interaction()
        categories = await rollset_cog._category_autocomplete(interaction, "")
        assert [choice.value for choice in categories] == ["map", "landmark"]

        interaction = _interaction()
        choices = await rollset_cog._item_autocomplete(interaction, "ze")
        assert [choice.value for choice in choices] == ["Zeta"]


class TestHelp:
    @pytest.mark.asyncio
    async def test_rollsets_topic(self, rollset_cog):
        interaction = _interaction()
        await rollset_cog.send_help(interaction, "rollsets")
        content = interaction.response.messages[0][0]
        assert "# Help: /rollsets" in content
        assert "/rollsets description update" in content
        assert "server managers" in content


class TestDeferredResponses:
    """Defer/respond contract, mirroring the matchmaking /games commands.

    Every /rollsets command defers (ephemerally) before it touches the
    database and then answers through the followup webhook. Validation
    errors stay on the initial response: they are raised pre-defer.
    """

    @pytest.mark.asyncio
    async def test_writes_defer_and_report_via_followup(
            self, rollset_cog, monkeypatch):
        monkeypatch.setattr(db_config, "add_category", _noop_return(True))
        interaction = _interaction()
        await MatchRolls.rollsets_add.callback(
            rollset_cog, interaction, "deck", "Standard")
        assert interaction.response.deferred is True
        assert interaction.followup.sent[0] == (
            "Roll category `deck` added (1 items).", True, None, None)

    @pytest.mark.asyncio
    async def test_reads_defer_and_report_via_followup(self, rollset_cog):
        interaction = _interaction()
        await MatchRolls.rollsets_list.callback(rollset_cog, interaction)
        assert interaction.response.deferred is True
        assert "# Roll sets" in interaction.followup.sent[0][0]

    @pytest.mark.asyncio
    async def test_validation_errors_precede_the_defer(self, rollset_cog):
        interaction = _interaction()
        await MatchRolls.rollsets_add.callback(
            rollset_cog, interaction, " ", "X")
        assert interaction.response.deferred is None
        assert "`category` must be" in interaction.response.messages[0][0]
        assert interaction.followup.sent == []

    @pytest.mark.asyncio
    async def test_remove_sends_its_confirm_view_via_followup(
            self, rollset_cog, monkeypatch):
        monkeypatch.setattr(db_config, "list_category_items", _noop_return(
            (["Zeta"], [])))
        monkeypatch.setattr(db_config, "item_variant_counts", _noop_return([]))
        interaction = _interaction()
        await MatchRolls.rollsets_remove.callback(rollset_cog, interaction, "map")
        assert interaction.response.deferred is True
        _, _, _, view = interaction.followup.sent[0]
        assert isinstance(view, ConfirmView)
