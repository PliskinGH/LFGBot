"""Tests for the admin configuration commands (LFGAdminMixin)."""
import configparser
from types import SimpleNamespace

import discord
import pytest

from cogs.matchmaking import constants, db_config, validation
from cogs.matchmaking.admin import LFGAdminMixin
from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.constants import DEFAULT_GUILD_ID
from cogs.matchmaking.models import GameOption, GuildGamesConfig
from common import constants as common_constants

from tests.conftest import FakeBot, FakeInteraction, FakeMember


async def _loaded_config() -> db_config.LoadedLFGConfig:
    """A minimal database-loaded config for refresh stubs."""
    loaded = db_config.LoadedLFGConfig()
    guild = GuildGamesConfig(42424)
    guild.games["game_a"] = GameOption(
        name="Game A", command="game_a", role="", icon="", color="",
        forum=None, channel=None, tag=None, visibility=None, message=None,
        registration_api=None, match_api=None, match_url=None,
        api_token=None, website_url=None, registration_url=None,
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
        assert validation.is_valid_command_name(name) is expected


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
        assert interaction.response.deferred is True
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.followup.sent[0][0] == "Game `root` added."

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
    async def test_channel_mention_is_stored_unchanged(self, monkeypatch):
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
            cog, interaction, command="root", channel="<#123>")
        guild_id, command, fields = written["add"]
        assert fields["channel"] == "<#123>"

    @pytest.mark.asyncio
    async def test_rejects_non_mention_channel(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "add_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_add.callback(
            cog, interaction, command="root", channel="1068560342671700088")
        assert calls == []
        assert "channel" in interaction.response.messages[0][0]
        assert "mention" in interaction.response.messages[0][0]

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
        assert "already configured" in interaction.followup.sent[0][0]

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


class TestGamesCopy:
    @pytest.mark.asyncio
    async def test_requires_manage_guild(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=FakeMember(1, "Rando"), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "copy_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game X")
        assert calls == []
        assert (interaction.response.messages[0][0]
                == "Only server managers can change the game configuration.")

    @pytest.mark.asyncio
    async def test_config_file_mode_is_read_only(self, monkeypatch):
        cog = _cog(monkeypatch, with_db=False)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "copy_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game X")
        assert calls == []
        assert "config-file mode" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_copies_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            return None

        async def fake_copy(guild_id, source, command, **kwargs):
            written["copy"] = (guild_id, source, command, kwargs)
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "copy_game", fake_copy)
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game X")
        guild_id, source, command, kwargs = written["copy"]
        assert (guild_id, source, command) == (42424, "game_a", "game_x")
        assert kwargs["name"] == "Game X"
        # The guild's commands are synced so the copied game works right away.
        assert interaction.response.deferred is True
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.followup.sent[0][0] == "Game `game_x` copied from `game_a`."

    @pytest.mark.asyncio
    async def test_overrides_are_forwarded(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            return None

        async def fake_copy(guild_id, source, command, **kwargs):
            written["copy"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "copy_game", fake_copy)
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game X",
            role="<@&954741722846490624>", max_players=4)
        # Only the provided options override the copied values; the display
        # name is always present.
        assert written["copy"]["name"] == "Game X"
        assert written["copy"]["role"] == "<@&954741722846490624>"
        assert written["copy"]["default_max_guests"] == 3

    @pytest.mark.asyncio
    async def test_missing_source_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "copy_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_copy.callback(
            cog, interaction, game="nope", command="game_x", name="Game X")
        assert calls == []
        assert "not configured" in interaction.response.messages[0][0]


    @pytest.mark.asyncio
    async def test_self_copy_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "copy_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_a", name="Game X")
        assert calls == []
        assert "must differ" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_identical_display_name_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "copy_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game A")
        assert calls == []
        assert "must differ" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_invalid_command_name(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "copy_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="Not Valid", name="Game X")
        assert calls == []
        assert "not a valid slash command name" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_invalid_name(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "copy_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="game:x")
        assert calls == []
        assert "game:" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_passes_api_field_overrides(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            return None

        async def fake_copy(guild_id, source, command, **kwargs):
            written["copy"] = kwargs
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "copy_game", fake_copy)
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game X",
            title_field="match_title", participants_field="players")
        assert written["copy"]["api_fields"] == {
            constants.API_TITLE_FIELD_KEY: "match_title",
            constants.API_PARTICIPANTS_FIELD_KEY: "players",
        }

    @pytest.mark.asyncio
    async def test_duplicate_command_rejected(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            return None

        async def fake_copy(*args, **kwargs):
            return False

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "copy_game", fake_copy)
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game X")
        assert "already configured" in interaction.followup.sent[0][0]

    @pytest.mark.asyncio
    async def test_refreshes_config_after_write(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_ensure(guild_id):
            return None

        async def fake_copy(*args, **kwargs):
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "copy_game", fake_copy)
        await Matchmaking.games_copy.callback(
            cog, interaction, game="game_a", command="game_x", name="Game X")
        assert 42424 in cog.guilds
        assert "game_a" in cog.guilds[42424].games
        assert cog.bot.tree.get_commands(guild=discord.Object(id=42424))

    @pytest.mark.asyncio
    async def test_game_autocomplete(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        choices = await cog.games_copy_game_autocomplete(interaction, "game_a")
        assert [(choice.name, choice.value) for choice in choices] == [
            ("game_a", "game_a")]


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
        assert interaction.response.deferred is True
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.followup.sent[0][0] == "Game `game_a` updated."

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
    async def test_updates_channel_mention(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = (guild_id, command, fields)
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", channel="<#456>")
        _, _, fields = written["update"]
        assert fields == {"channel": "<#456>"}

    @pytest.mark.asyncio
    async def test_rejects_non_mention_channel(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", channel="456")
        assert calls == []
        assert "channel" in interaction.response.messages[0][0]
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
        assert "not configured" in interaction.followup.sent[0][0]

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

    @pytest.mark.asyncio
    async def test_renames_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = (guild_id, command, fields)
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", new_command="game_renamed")
        guild_id, command, fields = written["update"]
        assert (guild_id, command) == (42424, "game_a")
        assert fields == {"new_command": "game_renamed"}
        # The renamed command is re-registered for the guild right away.
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.followup.sent[0][0] == (
            "Game `game_a` renamed to `game_renamed`.")

    @pytest.mark.asyncio
    async def test_rename_alone_is_enough_to_proceed(self, monkeypatch):
        # No other option is required: the rename is the update.
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []

        async def fake_update(*args, **kwargs):
            calls.append(1)
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", new_command="game_renamed")
        assert len(calls) == 1
        assert "renamed to" in interaction.followup.sent[0][0]

    @pytest.mark.asyncio
    async def test_rejects_invalid_new_command(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", new_command="Not Valid")
        assert calls == []
        assert "not a valid slash command name" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_new_command_equal_to_command(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", new_command="game_a")
        assert calls == []
        assert "must differ" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rejects_taken_new_command(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        cog.get_guild_config(42424).games["game_b"] = GameOption(
            name="Game B", command="game_b", role="", icon="", color="",
            forum=None, channel=None, tag=None, visibility=None, message=None,
            registration_api=None, match_api=None, match_url=None,
            api_token=None, website_url=None, registration_url=None,
            profile_url=None, default_max_guests=None)
        calls = []
        monkeypatch.setattr(db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", new_command="game_b")
        assert calls == []
        assert "already configured" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_rename_keeps_other_options(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = fields
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", new_command="game_renamed",
            name="New Name", max_players=4)
        assert written["update"] == {
            "name": "New Name", "default_max_guests": 3,
            "new_command": "game_renamed"}


class TestPreDeferHasNoDatabaseCalls:
    """Validation runs before the defer, so it must stay free of I/O.

    Every db_config entry point is replaced by a stub that records whether
    the interaction had been deferred by the time it was reached: a stub
    called pre-defer would mean a database round trip burns part of the 3s
    interaction budget before the ack, which can time out the command.
    """

    @staticmethod
    def _forbid_db_before_defer(monkeypatch, interaction, **results):
        """Make each db_config function assert it runs after the defer."""
        early = []

        def stub(name, result):
            async def call(*args, **kwargs):
                if (not interaction.response.deferred):
                    early.append(name)
                return result
            return call

        for name, result in results.items():
            monkeypatch.setattr(db_config, name, stub(name, result))

        async def refresh(_self):
            if (not interaction.response.deferred):
                early.append("reload_config")

        monkeypatch.setattr(LFGAdminMixin, "reload_config", refresh)
        return early

    @pytest.mark.asyncio
    async def test_update_validation_does_not_touch_the_database(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        # A valid rename that goes all the way through, plus every validation
        # path that must reject before deferring.
        # (options, whether the options are accepted and reach the database)
        cases = [
            ({"new_command": "game_renamed"}, True),
            ({"new_command": "game_b"}, True),
            ({"new_command": "game_a"}, False),
            ({"new_command": "Not Valid"}, False),
            ({"name": "x" * (constants.GAME_NAME_MAX + 1)}, False),
            ({"role": "123"}, False),
            ({"forum": "456"}, False),
            ({"channel": "789"}, False),
            ({"max_players": 1}, False),
            ({"title_field": "bad field"}, False),
            ({}, False),
        ]
        for options, accepted in cases:
            # A fresh interaction per case: the defer flag is per interaction.
            interaction = FakeInteraction(user=_manager(), guild_id=42424)
            early = self._forbid_db_before_defer(
                monkeypatch, interaction,
                update_game=True, ensure_guild_config=None,
                load_config_from_db=None, add_parameter=True)
            await Matchmaking.games_update.callback(
                cog, interaction, command="game_a", **options)
            assert early == [], f"{options} reached {early} before deferring"
            # Rejected options answer on the initial response and never defer;
            # accepted ones defer and go on to the database.
            if (accepted):
                assert interaction.response.deferred is True, options
            else:
                assert interaction.response.deferred is None, options
                assert interaction.followup.sent == []

    def test_rename_error_is_decided_by_the_commands_alone(self):
        # The pre-defer check of /games update reads the commands the guild
        # has configured, never the database.
        assert "not a valid slash command name" in validation.rename_error(
            "Not Valid", "game_a", ["game_a"])
        assert validation.rename_error("game_a", "game_a", ["game_a"]) == (
            "`new_command` must differ from `command`.")
        assert "already configured" in validation.rename_error(
            "game_taken", "game_a", ["game_a", "game_taken"])
        assert validation.rename_error("game_free", "game_a", ["game_a"]) is None

    @pytest.mark.asyncio
    async def test_copy_validation_does_not_touch_the_database(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        cases = [
            {"game": "game_a", "command": "game_x", "name": "Game X"},
            {"game": "missing", "command": "game_x", "name": "Game X"},
            {"game": "game_a", "command": "game_a", "name": "Game X"},
            {"game": "game_a", "command": "game_x", "name": "Game A"},
            {"game": "game_a", "command": "Not Valid", "name": "Game X"},
            {"game": "game_a", "command": "game_x", "name": "game:x"},
        ]
        for options in cases:
            interaction = FakeInteraction(user=_manager(), guild_id=42424)
            early = self._forbid_db_before_defer(
                monkeypatch, interaction,
                copy_game=True, ensure_guild_config=None,
                load_config_from_db=None, update_game=True)
            await Matchmaking.games_copy.callback(cog, interaction, **options)
            assert early == [], f"{options} reached {early} before deferring"


class TestGamesRemove:
    @pytest.mark.asyncio
    async def test_removes_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_delete(guild_id, command):
            return guild_id == 42424 and command == "game_a"

        monkeypatch.setattr(db_config, "delete_game", fake_delete)
        await Matchmaking.games_remove.callback(cog, interaction, command="game_a")
        assert interaction.response.deferred is True
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.followup.sent[0][0] == "Game `game_a` removed."

    @pytest.mark.asyncio
    async def test_missing_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)

        async def fake_delete(guild_id, command):
            return False

        monkeypatch.setattr(db_config, "delete_game", fake_delete)
        await Matchmaking.games_remove.callback(cog, interaction, command="game_a")
        assert "not configured" in interaction.followup.sent[0][0]

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
    async def test_games_are_sorted_by_display_name(self, monkeypatch):
        cog = _cog(monkeypatch)
        games = cog.get_guild_config(42424).games
        games["game_z"] = GameOption(
            name="AAA", command="game_z", role="", icon="", color="",
            forum=None, channel=None, tag=None, visibility=None, message=None,
            registration_api=None, match_api=None, match_url=None,
            api_token=None, website_url=None, registration_url=None,
            profile_url=None, default_max_guests=None)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_list.callback(cog, interaction)
        content = interaction.response.messages[0][0]
        # Insertion order is game_a then game_z, but "AAA" sorts first.
        assert content.index("`game_z`") < content.index("`game_a`")

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

    @pytest.mark.asyncio
    async def test_many_games_are_clipped_to_the_message_limit(self, monkeypatch):
        cog = _cog(monkeypatch)
        games = cog.get_guild_config(42424).games
        for index in range(60):
            games[f"game_{index:02d}"] = GameOption(
                name=f"Game number {index} " + "x" * 60,
                command=f"game_{index:02d}", role="<@&111>", icon="", color="",
                forum="<#123>", channel="<#124>", tag="", visibility="",
                message="",
                registration_api="", match_api="", match_url="", api_token="",
                website_url="", registration_url="", profile_url="",
                default_max_guests=3)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_list.callback(cog, interaction)
        content = interaction.response.messages[0][0]
        assert len(content) <= 2000
        # Whole lines are kept and the tail says how many were cut.
        assert content.endswith("more not shown")
        assert "`game_00`" in content
        assert "`game_59`" not in content


class TestGameApiToken:
    @pytest.mark.asyncio
    async def test_add_stores_token_value(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            return None

        async def fake_add(guild_id, command, **fields):
            written["add"] = fields
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_game", fake_add)
        await Matchmaking.games_add.callback(
            cog, interaction, command="root", api_token="secret-value")
        # The token VALUE is stored (a secret); there is no env-var name.
        assert written["add"]["api_token"] == "secret-value"
        assert interaction.followup.sent[0][0] == "Game `root` added."

    @pytest.mark.asyncio
    async def test_update_rotates_token(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = fields
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", api_token="rotated-secret")
        assert written["update"] == {"api_token": "rotated-secret"}
        assert interaction.followup.sent[0][0] == "Game `game_a` updated."

    @pytest.mark.asyncio
    async def test_update_dash_clears_token(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = fields
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", api_token=common_constants.RESET_SENTINEL)
        assert written["update"] == {"api_token": ""}

    @pytest.mark.asyncio
    async def test_list_does_not_mention_the_token(self, monkeypatch):
        # /games list stays a compact summary: the api token, not even its
        # set/not-set state, is only shown by /games show.
        cog = _cog(monkeypatch)
        cog.get_guild_config(42424).games["game_a"].api_token = "super-secret"
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_list.callback(cog, interaction)
        message = interaction.response.messages[0][0]
        assert "api token" not in message
        assert "super-secret" not in message


def _embeds_text(embeds) -> str:
    """All textual content of a list of embeds, for leak assertions."""
    parts = []
    for embed in embeds:
        parts.append(embed.title or "")
        parts.append(embed.description or "")
        for field in embed.fields:
            parts.append(field.name)
            parts.append(field.value)
    return "\n".join(parts)


class TestGamesShow:
    @pytest.mark.asyncio
    async def test_requires_manage_guild(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=FakeMember(1, "Rando"), guild_id=42424)
        await Matchmaking.games_show.callback(cog, interaction, game="game_a")
        assert (interaction.response.messages[0][0]
                == "Only server managers can change the game configuration.")

    @pytest.mark.asyncio
    async def test_missing_game(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        await Matchmaking.games_show.callback(cog, interaction, game="game_x")
        assert ("`game_x` is not configured for this server."
                in interaction.response.messages[0][0])

    @pytest.mark.asyncio
    async def test_shows_everything_but_not_the_token_value(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        cog.guilds[42424].games["game_a"] = GameOption(
            name="Game A", command="game_a", role="<@&111>",
            icon="https://cdn.example/icon.png", color="16777215",
            forum="<#123>", channel="<#124>", tag="98765", visibility="0",
            message="Please check the rules.",
            registration_api="https://reg.example",
            match_api="https://api.example",
            match_url="https://match.example", api_token="super-secret",
            website_url="https://site.example",
            registration_url="https://signup.example",
            profile_url="https://profile.example",
            default_max_guests=4)
        cog.game_parameters[DEFAULT_GUILD_ID]["game_a"]["param1"][
            "display_name"] = "Param One"
        await Matchmaking.games_show.callback(cog, interaction, game="game_a")
        content, embeds, ephemeral, _ = interaction.response.messages[0]
        assert content == (
            "**Game A** — `/game_a`\n"
            "Icon: https://cdn.example/icon.png\n"
            "Color: 16777215\n"
            "Role to ping: <@&111>\n"
            "LFG channel: <#124>\n"
            "Forum: <#123> (tag: 98765)\n"
            "Threads: private\n"
            'Extra message: "Please check the rules."\n'
            "Default max players: 5")
        assert ephemeral is True
        submission_embed, parameters_embed = embeds
        assert submission_embed.title == "Match submission"
        fields = {field.name: field.value for field in submission_embed.fields}
        assert "LFG post" not in fields
        assert fields["Registration API"] == "https://reg.example"
        assert fields["Match API"] == "https://api.example"
        assert fields["Match URL"] == "https://match.example"
        assert fields["API token"] == "set"
        assert fields["Website URL"] == "https://site.example"
        assert fields["Registration URL"] == "https://signup.example"
        assert fields["Profile URL"] == "https://profile.example"
        assert parameters_embed.title == "Parameters — /game_a"
        parameter_fields = {field.name: field.value
                            for field in parameters_embed.fields}
        assert parameter_fields["param1 (Param One)"] == (
            "alpha (Alpha One), beta (Beta Two)\nSent as `field_one`")
        # The token value itself is a secret and must never appear anywhere.
        assert "super-secret" not in content
        assert "super-secret" not in _embeds_text(embeds)

    @pytest.mark.asyncio
    async def test_minimal_game_and_discord_only_parameter(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        cog.game_api_fields[DEFAULT_GUILD_ID]["game_a"].clear()
        await Matchmaking.games_show.callback(cog, interaction, game="game_a")
        content, embeds, _, _ = interaction.response.messages[0]
        # Only the game header is left: no LFG post settings are configured.
        assert content == "**Game A** — `/game_a`"
        submission_embed, parameters_embed = embeds
        assert submission_embed.title == "Match submission"
        fields = {field.name: field.value for field in submission_embed.fields}
        assert "Registration API" not in fields
        assert fields["API token"] == "not set"
        field = parameters_embed.fields[0]
        assert field.name == "param1"
        assert field.value == "alpha (Alpha One), beta (Beta Two)\nDiscord-only"
        assert "Sent as" not in _embeds_text(embeds)

    @pytest.mark.asyncio
    async def test_show_lists_payload_fields(self, monkeypatch):
        # The reserved match-payload fields: defaults plus the game's own
        # overrides, merged like register_match does.
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        cog.default_api_fields[constants.API_TITLE_FIELD_KEY] = "match_title"
        cog.game_api_fields[DEFAULT_GUILD_ID]["game_a"][
            constants.API_PARTICIPANTS_FIELD_KEY] = "players"
        await Matchmaking.games_show.callback(cog, interaction, game="game_a")
        _, embeds, _, _ = interaction.response.messages[0]
        fields = {field.name: field.value for field in embeds[0].fields}
        assert (fields["Payload fields"]
                == "title → `match_title` · participants → `players`")

    @pytest.mark.asyncio
    async def test_show_without_parameters_sends_a_single_embed(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        cog.game_parameters[DEFAULT_GUILD_ID]["game_a"].clear()
        await Matchmaking.games_show.callback(cog, interaction, game="game_a")
        content, embeds, _, _ = interaction.response.messages[0]
        assert len(embeds) == 1
        assert embeds[0].title == "Match submission"
        # With no parameters there is no second embed: the content says so.
        assert content.endswith("Parameters: none")

    @pytest.mark.asyncio
    async def test_long_values_are_truncated_to_embed_limits(self, monkeypatch):
        # Very long configured URLs must not break the embed: every value is
        # cut to Discord's per-field limits (1024 value, 256 name).
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        cog.guilds[42424].games["game_a"].registration_api = (
            "https://reg.example/" + "a" * 3000)
        cog.game_parameters[DEFAULT_GUILD_ID]["game_a"]["param1"][
            "display_name"] = "P" * 300
        await Matchmaking.games_show.callback(cog, interaction, game="game_a")
        _, embeds, _, _ = interaction.response.messages[0]
        fields = {field.name: field.value for field in embeds[0].fields}
        assert len(fields["Registration API"]) == 1024
        assert fields["Registration API"].endswith("…")
        parameter_field = embeds[1].fields[0]
        assert len(parameter_field.name) == 256
        assert parameter_field.name.startswith("param1 (PPP")


class TestGameApiFields:
    """The reserved match-payload component fields (title, participants...)."""

    @pytest.mark.asyncio
    async def test_add_stores_reserved_fields(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_ensure(guild_id):
            return None

        async def fake_add(guild_id, command, **fields):
            written["add"] = fields
            return True

        monkeypatch.setattr(db_config, "ensure_guild_config", fake_ensure)
        monkeypatch.setattr(db_config, "add_game", fake_add)
        await Matchmaking.games_add.callback(
            cog, interaction, command="root",
            title_field="match_title", participants_field="players")
        assert written["add"]["api_fields"] == {
            constants.API_TITLE_FIELD_KEY: "match_title",
            constants.API_PARTICIPANTS_FIELD_KEY: "players",
        }
        assert interaction.followup.sent[0][0] == "Game `root` added."

    @pytest.mark.asyncio
    async def test_update_sets_reserved_fields(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = fields
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", title_field="renamed_title")
        # No game columns were touched: only the api-field override changed.
        assert written["update"] == {
            "api_fields": {constants.API_TITLE_FIELD_KEY: "renamed_title"}}
        assert interaction.followup.sent[0][0] == "Game `game_a` updated."

    @pytest.mark.asyncio
    async def test_update_dash_clears_reserved_field(self, monkeypatch):
        # "-" removes the override: the game falls back to the default field.
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = fields
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", table_talk_url_field=common_constants.RESET_SENTINEL)
        assert written["update"] == {
            "api_fields": {constants.API_TABLE_TALK_URL_FIELD_KEY: None}}

    @pytest.mark.asyncio
    async def test_update_rejects_invalid_field_name(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        calls = []
        monkeypatch.setattr(
            db_config, "update_game", lambda *a, **k: calls.append(1))
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", title_field="bad field")
        assert calls == []
        assert "title_field" in interaction.response.messages[0][0]

    @pytest.mark.asyncio
    async def test_unset_arguments_keep_current_overrides(self, monkeypatch):
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=_manager(), guild_id=42424)
        written = {}

        async def fake_update(guild_id, command, **fields):
            written["update"] = fields
            return True

        monkeypatch.setattr(db_config, "update_game", fake_update)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_a", api_token="rotated")
        # api_fields is not passed: db_config keeps the current overrides.
        assert written["update"] == {"api_token": "rotated"}


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
        error = validation.parameter_error(name, values)
        assert (error is None) is valid

    @pytest.mark.parametrize("api_field,valid", [
        ("field_one", True), ("field1", True), ("Field_One", True),
        ("", True),  # empty resets/clears the API field
        ("bad field", False), ("bad-field", False),
        ("bad.field", False), ("   ", False),
    ])
    def test_api_field_error(self, api_field, valid):
        error = validation.parameter_error("newparam", "a, b", api_field=api_field)
        assert (error is None) is valid

    @pytest.mark.parametrize("display_name,valid", [
        ("Map", True), ("Map Pool", True), ("map", True),
        ("", True),  # empty resets to the name
        ("   ", False), ("a" * 51, False), ("line\nbreak", False),
    ])
    def test_display_name_error(self, display_name, valid):
        error = validation.parameter_error(
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
        assert interaction.response.deferred is True
        assert cog.bot.tree.sync_calls == [42424]
        assert (interaction.followup.sent[0][0]
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
        assert "already has a parameter" in interaction.followup.sent[0][0]

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
        assert interaction.response.deferred is True
        assert cog.bot.tree.sync_calls == [42424]
        assert interaction.followup.sent[0][0] == "Parameter `param1` updated."

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
        assert interaction.followup.sent[0][0] == "Parameter `param1` updated."

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
            cog, interaction, game="game_a", name="param1", api_field=common_constants.RESET_SENTINEL)
        assert written["update"] == {"values": None, "api_field": ""}
        assert interaction.followup.sent[0][0] == "Parameter `param1` updated."

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
            cog, interaction, game="game_a", name="param1", display_name=common_constants.RESET_SENTINEL)
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
        assert "has no parameter named" in interaction.followup.sent[0][0]

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
        assert interaction.response.deferred is True
        assert cog.bot.tree.sync_calls == [42424]
        assert (interaction.followup.sent[0][0]
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
        assert "has no parameter named" in interaction.followup.sent[0][0]

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

