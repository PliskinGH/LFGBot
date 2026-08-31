"""Tests for the admin configuration commands (AdminMixin)."""
import configparser
from types import SimpleNamespace

import discord
import pytest

from cogs.matchmaking import db_config
from cogs.matchmaking.cog import Matchmaking
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
    return loaded


def _cog(monkeypatch, with_db=True) -> Matchmaking:
    config = configparser.ConfigParser()
    config.read_string(
        "[DEFAULT]\nGamesCommands = game_a\nGamesFullNames = Game A\n"
        "[GuildA]\nID = 42424\nGamesCommands = game_a\nGamesFullNames = Game A\n"
    )
    bot = FakeBot()
    if (with_db):
        bot.db = SimpleNamespace(fresh=False)
    monkeypatch.setattr(db_config, "load_config_from_db", _loaded_config)
    return Matchmaking(bot=bot, config=config)


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
    async def test_available_to_non_managers(self, monkeypatch):
        # /games list is read-only: unlike the other subcommands it is not
        # gated behind the manage_guild permission.
        cog = _cog(monkeypatch)
        interaction = FakeInteraction(user=FakeMember(1, "Rando"), guild_id=42424)
        await Matchmaking.games_list.callback(cog, interaction)
        content = interaction.response.messages[0][0]
        assert "game_a" in content
        assert "Only server managers" not in content

