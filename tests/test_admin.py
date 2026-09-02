"""Tests for the admin configuration commands (AdminMixin)."""
import configparser
from types import SimpleNamespace

import discord
import pytest

from cogs.matchmaking import db_config
from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.constants import DEFAULT_GUILD_ID
from cogs.matchmaking.models import GameOption, GuildGamesConfig

from tests.conftest import FakeBot, FakeInteraction, FakeMember


async def _loaded_config() -> db_config.LoadedConfig:
    """A minimal database-loaded config for refresh stubs."""
    loaded = db_config.LoadedConfig()
    guild = GuildGamesConfig(42424)
    guild.games["game_a"] = GameOption(
        name="Game A", command="game_a", role="", icon="", color="",
        forum=None, tag=None, visibility=None, message=None,
        registration_api=None, match_api=None, match_url=None,
        api_token_env_var=None, website_url=None, registration_url=None,
        profile_url=None, default_max_guests=None)
    loaded.guilds[42424] = guild
    # One game parameter (with its match API field), held under the DEFAULT
    # sentinel: guilds without their own entry inherit these definitions.
    loaded.game_parameters[DEFAULT_GUILD_ID] = {
        "game_a": {"param1": {"display_name": "param1",
                              "values": {"alpha": "Alpha One", "beta": "Beta Two"}}}}
    loaded.game_api_fields[DEFAULT_GUILD_ID] = {
        "game_a": {"param1": "field_one"}}
    return loaded


def _cog(monkeypatch, with_db=True) -> Matchmaking:
    config = configparser.ConfigParser()
    config.read_string(
        "[DEFAULT]\nGamesCommands = game_a\nGamesFullNames = Game A\n"
        "[GuildA]\nID = 42424\nGamesCommands = game_a\nGamesFullNames = Game A\n"
    )
    game_parameters = configparser.ConfigParser()
    game_parameters.read_string(
        "[game_a]\n"
        "param1 = field_one: (alpha, Alpha One), (beta, Beta Two)\n"
    )
    bot = FakeBot()
    if (with_db):
        bot.db = SimpleNamespace(fresh=False)
    monkeypatch.setattr(db_config, "load_config_from_db", _loaded_config)
    return Matchmaking(bot=bot, config=config, game_parameters=game_parameters)


def _manager(user_id=1) -> FakeMember:
    member = FakeMember(user_id, "Manager")
    member.guild_permissions = SimpleNamespace(manage_guild=True)
    return member


class TestIsValidCommandName:
    @pytest.mark.parametrize("name,expected", [
        ("root", True), ("root_tts", True), ("rdl1", True),
        ("Root", False), ("not valid", False), ("c&c", False), ("", False),
        ("a" * 33, False),
    ])
    def test_validity(self, name, expected):
        assert Matchmaking.is_valid_command_name(name) is expected


class TestGamesAdd:
    @pytest.mark.asyncio
    async def test_requires_manage_guild(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=FakeMember(1, "Rando"), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_add.callback(cog, interaction, command="newgame")
        assert calls == []
        assert (interaction.response.messages[0][0]
                == "Only server managers can change the game configuration.")

    @pytest.mark.asyncio
    async def test_config_file_mode_is_read_only(self, monkeypatch):
        cog = _cog(monkeypatch, with_db=False)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_add.callback(cog, interaction, command="newgame")
        assert calls == []
        assert "config-file mode" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_adds_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_add(guild_id, command, **fields):
            written["add"] = (guild_id, command, fields)
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_game", fake_add)
        await Matchmaking.games_add.callback(
            cog, interaction, command="root", name="Root",
            role="<@&954741722846490624>",
            forum="<#1068560342671700088>", max_players=4)
        assert written["ensure"] == 42424
        guild_id, command, fields = written["add"]
        assert (guild_id, command) == (42424, "root")
        assert fields["name"] == "Root"
        assert fields["default_max_guests"] == 3
        # Roles and forum channels are stored as mentions.
        assert fields["role"] == "<@&954741722846490624>"
        assert fields["forum"] == "<#1068560342671700088>"
        # The guild's commands are synced so the new game works right away.
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.response.messages[0][0] == "Game `root` added."

    @pytest.mark.asyncio
    async def test_forum_mention_is_stored_unchanged(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            return None

        async def fake_add(guild_id, command, **fields):
            written["add"] = (guild_id, command, fields)
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_game", fake_add)
        await Matchmaking.games_add.callback(
            cog, interaction, command="root", forum="<#123>")
        guild_id, command, fields = written["add"]
        assert fields["forum"] == "<#123>"

    @pytest.mark.asyncio
    async def test_rejects_non_mention_role(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_add.callback(
            cog, interaction, command="root", role="954741722846490624")
        assert calls == []
        assert "role" in interaction.response.messages[0][0]
        assert "mention" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_non_mention_forum(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_add.callback(
            cog, interaction, command="root", forum="1068560342671700088")
        assert calls == []
        assert "forum" in interaction.response.messages[0][0]
        assert "mention" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_duplicate_game_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            return None

        async def fake_add(guild_id, command, **fields):
            return False

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_game", fake_add)
        await Matchmaking.games_add.callback(cog, interaction, command="root")
        assert "already configured" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_invalid_command_name(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_add.callback(cog, interaction, command="Not Valid")
        assert calls == []
        assert "not a valid slash command name" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_max_players(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_add.callback(cog, interaction, command="root", max_players=1)
        assert calls == []
        assert "max_players" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_refreshes_config_after_write(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            return None

        async def fake_add(*args, **kwargs):
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_game", fake_add)
        await Matchmaking.games_add.callback(cog, interaction, command="root")
        # The in-memory configuration was replaced by the fake reload, and the
        # per-guild commands were re-registered.
        assert 42424 in cog.guilds
        assert "game_a" in cog.guilds[42424].games
        assert cog.bot.tree.get_commands(guild=discord.Object(id=42424))


class TestGamesUpdate:
    @pytest.mark.asyncio
    async def test_updates_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = (guild_id, command, fields)
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", name="New Name", max_players=4)
        guild_id, command, fields = written["update"]
        assert (guild_id, command) == (42424, "game_a")
        assert fields == {"name": "New Name", "default_max_guests": 3}
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.response.messages[0][0] == "Game `game_a` updated."

    @pytest.mark.asyncio
    async def test_rejects_non_mention_role(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", role="123")
        assert calls == []
        assert "role" in interaction.response.messages[0][0]
        assert "mention" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_non_mention_forum(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", forum="1068560342671700088")
        assert calls == []
        assert "forum" in interaction.response.messages[0][0]
        assert "mention" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_missing_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_update(guild_id, command, **fields):
            return False

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", name="X")
        assert "not configured" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_nothing_to_update(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(cog, interaction, command="game_a")
        assert calls == []
        assert "Nothing to update" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_autocomplete_lists_games(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        choices = await cog.games_update_command_autocomplete(interaction, "game_a")
        assert [(choice.name, choice.value) for choice in choices] == [("game_a", "game_a")]


class TestGamesRemove:
    @pytest.mark.asyncio
    async def test_removes_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_delete(guild_id, command):
            return guild_id == 42424 and command == "game_a"

        monkeypatch.setattr(db_config, "delete_game", fake_delete)
        await Matchmaking.games_remove.callback(cog, interaction, command="game_a")
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.response.messages[0][0] == "Game `game_a` removed."

    @pytest.mark.asyncio
    async def test_missing_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_delete(guild_id, command):
            return False

        monkeypatch.setattr(db_config, "delete_game", fake_delete)
        await Matchmaking.games_remove.callback(cog, interaction, command="game_a")
        assert "not configured" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_autocomplete_lists_games(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        choices = await cog.games_remove_command_autocomplete(interaction, "game_a")
        assert [(choice.name, choice.value) for choice in choices] == [("game_a", "game_a")]


class TestGamesList:
    @pytest.mark.asyncio
    async def test_lists_games(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_list.callback(cog, interaction)
        content = interaction.response.messages[0][0]
        assert "game_a" in content
        assert "Game A" in content

    @pytest.mark.asyncio
    async def test_requires_manage_guild(self, monkeypatch):
        # /games list exposes api_fields and league settings, so it is gated
        # behind manage_guild like the rest of the /games group.
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=FakeMember(1, "Rando"), guild_id=42424)
        await Matchmaking.games_list.callback(cog, interaction)
        content = interaction.response.messages[0][0]
        assert content == "Only server managers can change the game configuration."
        assert "game_a" not in content


class TestParameterError:
    @pytest.mark.parametrize("name,values,valid", [
        ("newparam", "a, b", True),
        ("newparam", "(a, Alpha), b", True),
        ("Bad Name", "a, b", False),
        ("UPPER", "a, b", False),
        ("api_title_field", "a, b", False),
        ("a" * 33, "a, b", False),
        ("newparam", "", False),
        ("newparam", "   ", False),
        ("newparam", ",", False),
    ])
    def test_parameter_error(self, name, values, valid):
        error = Matchmaking._parameter_error(name, values)
        assert (error is None) is valid

    @pytest.mark.parametrize("api_field,valid", [
        ("field_one", True), ("field1", True), ("Field_One", True),
        ("", True),  # empty resets/clears the API field
        ("bad field", False), ("bad-field", False),
        ("bad.field", False), ("   ", False),
    ])
    def test_api_field_error(self, api_field, valid):
        error = Matchmaking._parameter_error("newparam", "a, b", api_field=api_field)
        assert (error is None) is valid

    @pytest.mark.parametrize("display_name,valid", [
        ("Map", True), ("Map Pool", True), ("map", True),
        ("", True),  # empty resets to the name
        ("   ", False), ("a" * 51, False), ("line\nbreak", False),
    ])
    def test_display_name_error(self, display_name, valid):
        error = Matchmaking._parameter_error(
            "newparam", "a, b", display_name=display_name)
        assert (error is None) is valid


class TestGamesParameterAdd:
    @pytest.mark.asyncio
    async def test_requires_manage_guild(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=FakeMember(1, "Rando"), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="game_a", name="newparam", values="a, b")
        assert calls == []
        assert (interaction.response.messages[0][0]
                == "Only server managers can change the game configuration.")

    @pytest.mark.asyncio
    async def test_config_file_mode_is_read_only(self, monkeypatch):
        cog = _cog(monkeypatch, with_db=False)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="game_a", name="newparam", values="a, b")
        assert calls == []
        assert "config-file mode" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_adds_parameter(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_add(guild_id, game, name, values, api_field=None,
                           display_name=None):
            written["add"] = (guild_id, game, name, values, api_field, display_name)
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_parameter", fake_add)
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="game_a", name="newparam",
            values="(a, Alpha), b", api_field="new_field",
            display_name="New Param")
        assert written["add"] == (
            42424, "game_a", "newparam", {"a": "Alpha", "b": "b"},
            "new_field", "New Param")
        assert cog.bot.tree.sync_calls == [42424]
        assert (interaction.response.messages[0][0]
                == "Parameter `newparam` added to `game_a`.")

    @pytest.mark.asyncio
    async def test_missing_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="missing", name="newparam", values="a")
        assert calls == []
        assert "not configured" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_duplicate_parameter_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            pass

        async def fake_add(guild_id, game, name, values, api_field=None,
                           display_name=None):
            return False

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_parameter", fake_add)
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="game_a", name="param1", values="x, y")
        assert "already has a parameter" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_invalid_api_field_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="game_a", name="newparam",
            values="a, b", api_field="bad field")
        assert calls == []
        assert "api_field" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_invalid_display_name_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="game_a", name="newparam",
            values="a, b", display_name="line\nbreak")
        assert calls == []
        assert "display_name" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_game_autocomplete(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        choices = await cog.games_parameter_add_game_autocomplete(interaction, "game_a")
        assert [(c.name, c.value) for c in choices] == [("game_a", "game_a")]


class TestGamesParameterUpdate:
    @pytest.mark.asyncio
    async def test_updates_values_and_api_field(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_update(guild_id, game, name, **kwargs):
            written["update"] = (guild_id, game, name, kwargs)
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1",
            values="zed, y", api_field="renamed", display_name="Renamed Param")
        assert written["update"] == (
            42424, "game_a", "param1",
            {"values": {"zed": "zed", "y": "y"}, "api_field": "renamed",
             "display_name": "Renamed Param"})
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.response.messages[0][0] == "Parameter `param1` updated."

    @pytest.mark.asyncio
    async def test_values_only_update_omits_api_field(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_update(guild_id, game, name, **kwargs):
            written["update"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1", values="zed")
        # api_field is not passed: db_config keeps the current one.
        assert written["update"] == {"values": {"zed": "zed"}}

    @pytest.mark.asyncio
    async def test_api_field_only_update(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_update(guild_id, game, name, **kwargs):
            written["update"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1", api_field="renamed")
        assert written["update"] == {"values": None, "api_field": "renamed"}

    @pytest.mark.asyncio
    async def test_blank_api_field_resets(self, monkeypatch):
        # An empty api_field reaches db_config as "": the DB layer turns it
        # into NULL, clearing the mapping (Discord-only parameter again).
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_update(guild_id, game, name, **kwargs):
            written["update"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1", api_field="")
        assert written["update"] == {"values": None, "api_field": ""}
        assert interaction.response.messages[0][0] == "Parameter `param1` updated."

    @pytest.mark.asyncio
    async def test_dash_resets_api_field(self, monkeypatch):
        # Discord cannot send an empty string: "-" is the reset sentinel,
        # normalized to "" (which db_config turns into NULL).
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_update(guild_id, game, name, **kwargs):
            written["update"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1", api_field="-")
        assert written["update"] == {"values": None, "api_field": ""}
        assert interaction.response.messages[0][0] == "Parameter `param1` updated."

    @pytest.mark.asyncio
    async def test_dash_resets_display_name(self, monkeypatch):
        # "-" resets the label back to the parameter name (db_config turns
        # "" into the name).
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_update(guild_id, game, name, **kwargs):
            written["update"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1", display_name="-")
        assert written["update"] == {"values": None, "display_name": ""}

    @pytest.mark.asyncio
    async def test_display_name_only_update(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            written["ensure"] = guild_id

        async def fake_update(guild_id, game, name, **kwargs):
            written["update"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1",
            display_name="Map Pool")
        # values/api_field are not passed: db_config keeps the current ones.
        assert written["update"] == {"values": None, "display_name": "Map Pool"}

    @pytest.mark.asyncio
    async def test_invalid_api_field_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1", api_field="bad field")
        assert calls == []
        assert "api_field" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_nothing_to_update(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1")
        assert calls == []
        assert "Nothing to update" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_empty_values_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="param1", values="  ")
        assert calls == []
        assert "at least one value" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_missing_parameter(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            pass

        async def fake_update(guild_id, game, name, **kwargs):
            return False

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "update_parameter", fake_update)
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="game_a", name="nope", values="a")
        assert "has no parameter named" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_missing_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_update.callback(
            cog, interaction, game="missing", name="param1", values="a")
        assert calls == []
        assert "not configured" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_name_autocomplete(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        interaction.namespace = SimpleNamespace(game="game_a")
        choices = await cog.games_parameter_update_name_autocomplete(
            interaction, "param")
        assert [(c.name, c.value) for c in choices] == [("param1", "param1")]


class TestGamesParameterRemove:
    @pytest.mark.asyncio
    async def test_removes_parameter(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            pass

        async def fake_delete(guild_id, game, name):
            return guild_id == 42424 and game == "game_a" and name == "param1"

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "delete_parameter", fake_delete)
        await Matchmaking.games_parameter_remove.callback(
            cog, interaction, game="game_a", name="param1")
        assert cog.bot.tree.sync_calls == [42424]
        assert (interaction.response.messages[0][0]
                == "Parameter `param1` removed from `game_a`.")

    @pytest.mark.asyncio
    async def test_missing_parameter(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            pass

        async def fake_delete(guild_id, game, name):
            return False

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "delete_parameter", fake_delete)
        await Matchmaking.games_parameter_remove.callback(
            cog, interaction, game="game_a", name="param1")
        assert "has no parameter named" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_missing_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "delete_parameter", lambda *a, **k: calls.append(1))
        await Matchmaking.games_parameter_remove.callback(
            cog, interaction, game="missing", name="param1")
        assert calls == []
        assert "not configured" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_name_autocomplete(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        interaction.namespace = SimpleNamespace(game="game_a")
        choices = await cog.games_parameter_remove_name_autocomplete(
            interaction, "param")
        assert [(c.name, c.value) for c in choices] == [("param1", "param1")]


class TestGamesParameterList:
    @pytest.mark.asyncio
    async def test_lists_parameters(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_parameter_list.callback(
            cog, interaction, game="game_a")
        content = interaction.response.messages[0][0]
        assert "param1" in content
        assert "field_one" in content
        assert "Alpha One" in content

    @pytest.mark.asyncio
    async def test_lists_display_name(self, monkeypatch):
        cog = _cog(monkeypatch)
        cog.game_parameters[DEFAULT_GUILD_ID]["game_a"]["param1"]["display_name"] = (
            "Map Pool")
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_parameter_list.callback(
            cog, interaction, game="game_a")
        content = interaction.response.messages[0][0]
        assert "param1 (Map Pool)" in content

    @pytest.mark.asyncio
    async def test_requires_manage_guild(self, monkeypatch):
        # Gated behind manage_guild like the rest of the /games group
        # (it exposes api_fields).
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=FakeMember(1, "Rando"), guild_id=42424)
        await Matchmaking.games_parameter_list.callback(
            cog, interaction, game="game_a")
        content = interaction.response.messages[0][0]
        assert content == "Only server managers can change the game configuration."
        assert "param1" not in content

    @pytest.mark.asyncio
    async def test_no_parameters(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_parameter_list.callback(
            cog, interaction, game="missing")
        assert "has no parameters" in interaction.response.messages[0][0]

