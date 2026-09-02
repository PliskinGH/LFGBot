"""Tests for the database-backed configuration (db/ + cogs/matchmaking/db_config.py).

Database tests run against TEST_DATABASE_URL — the production DATABASE_URL is
never used for tests. The test database must be provisioned beforehand
(CI's Postgres service does it); its schema is emptied per test. Tests skip
when TEST_DATABASE_URL is unset or the server is unreachable. The pure
parsing/mapping tests run everywhere.
"""
import configparser
import os

import pytest
from tortoise import connections
from tortoise.migrations.api.migrate import migrate as apply_migrations

from db import models
from db.db import Database
from db.orm_config import orm_config

from cogs.matchmaking import db_config
from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.constants import DEFAULT_GUILD_ID

from tests.conftest import FakeBot


def _test_database_url() -> str | None:
    """The URL of the test database, or None when unconfigured."""
    return os.getenv("TEST_DATABASE_URL")


def _safe_url(url: str) -> str:
    """A URL with the password masked, for error messages."""
    scheme, _, rest = url.partition("://")
    return f"{scheme}://***@{rest.partition('@')[2]}"


async def _drop_all_tables():
    """Drop the bot's tables and the migration history, for a clean test run."""
    await connections.get("default").execute_script(
        "DROP TABLE IF EXISTS tortoise_migrations, game_parameter_values, "
        "game_parameters, game_api_field_overrides, default_api_fields, "
        "games, guilds CASCADE")


@pytest.fixture
async def db():
    """A Database on the test database, from a clean migrated schema.

    The test database is provisioned by the environment (CI service or local
    Postgres). Like the deploy step, the schema is built from the committed
    migrations; each test drops it and re-applies them for isolation.
    """
    url = _test_database_url()
    if (not url):
        pytest.skip("No TEST_DATABASE_URL configured; skipping database tests.")
    database = Database(url)
    try:
        # Schema is built from the committed migrations (the deploy step);
        # each test drops it and re-applies them for isolation.
        await apply_migrations(config=orm_config(url))
        await _drop_all_tables()
        await apply_migrations(config=orm_config(url))
        await database.initialize()
    except Exception as error:
        await database.close()
        pytest.skip(f"Database at {_safe_url(url)} is unreachable: {error}")
    yield database
    await database.close()



def _assert_same_guild_config(expected, actual):
    """GuildGamesConfig/GameOption use identity equality, so compare deeply."""
    assert actual.guild_id == expected.guild_id
    assert list(actual.games.keys()) == list(expected.games.keys())
    for command, expected_option in expected.games.items():
        assert vars(actual.games[command]) == vars(expected_option)


def _assert_same_config(expected, actual):
    _assert_same_guild_config(expected.default_guild_config, actual.default_guild_config)
    assert set(actual.guilds.keys()) == set(expected.guilds.keys())
    for guild_id, expected_guild in expected.guilds.items():
        _assert_same_guild_config(expected_guild, actual.guilds[guild_id])
    assert actual.game_parameters == expected.game_parameters
    assert actual.game_api_fields == expected.game_api_fields
    assert actual.default_api_fields == expected.default_api_fields


class TestLoadedConfigFromIni:
    def test_matches_file_parsing(self, games_config, game_parameters_config):
        loaded = db_config.loaded_config_from_ini(games_config, game_parameters_config)
        # Sections without an ID key are skipped; [DEFAULT] is the fallback.
        assert set(loaded.guilds.keys()) == {90401}
        assert loaded.default_guild_config.guild_id is None
        assert list(loaded.default_guild_config.games.keys()) == ["game_a", "game_b"]
        assert list(loaded.guilds[90401].games.keys()) == ["game_c"]
        # Only games with a games_parameters.ini section appear here. From
        # the config files, every guild shares the definitions held under the
        # DEFAULT sentinel guild id.
        assert list(loaded.game_parameters[DEFAULT_GUILD_ID].keys()) == ["game_a"]
        assert loaded.default_api_fields == {}

    def test_parameter_and_api_field_parsing(self, games_config, game_parameters_config):
        loaded = db_config.loaded_config_from_ini(games_config, game_parameters_config)
        params = loaded.game_parameters[DEFAULT_GUILD_ID]["game_a"]
        api_fields = loaded.game_api_fields[DEFAULT_GUILD_ID]["game_a"]
        # Reserved api_* keys land in game_api_fields, not in the parameters.
        assert api_fields["api_title_field"] == "match_title"
        assert "api_title_field" not in params
        # param1/param2 are submitted to the match API; param3 is Discord-only.
        assert api_fields["param1"] == "field_one"
        assert api_fields["param2"] == "field_two"
        assert "param3" not in api_fields
        assert params["param3"] == {"display_name": "param3",
                                     "values": {"yes": "yes", "no": "no"}}


class TestCogFromLoadedConfig:
    """A cog built from a LoadedConfig matches the file-parsing cog."""

    def test_attributes_match_file_parsing(self, games_config, game_parameters_config):
        from_files = Matchmaking(bot=FakeBot(), config=games_config,
                                 game_parameters=game_parameters_config)
        loaded = db_config.loaded_config_from_ini(games_config, game_parameters_config)
        from_loaded = Matchmaking(bot=FakeBot(), loaded_config=loaded)

        _assert_same_guild_config(from_files.default_guild_config,
                                  from_loaded.default_guild_config)
        assert list(from_loaded.guilds.keys()) == list(from_files.guilds.keys())
        for guild_id, file_guild in from_files.guilds.items():
            _assert_same_guild_config(file_guild, from_loaded.guilds[guild_id])
        assert from_loaded.game_parameters == from_files.game_parameters
        assert from_loaded.game_api_fields == from_files.game_api_fields
        assert from_loaded.default_api_fields == from_files.default_api_fields

    def test_get_guild_config_falls_back_to_default(self, games_config, game_parameters_config):
        loaded = db_config.loaded_config_from_ini(games_config, game_parameters_config)
        cog = Matchmaking(bot=FakeBot(), loaded_config=loaded)
        assert cog.get_guild_config(999999) is cog.default_guild_config
        assert cog.get_guild_config(90401) is cog.guilds[90401]
        assert "game_c" in cog.get_guild_config(90401).games

class TestInitialize:
    async def test_empty_database_is_fresh(self, db):
        assert db.fresh is True

    async def test_seeded_database_is_not_fresh(self, db, games_config,
                                                game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        # A second connection to the same test database sees the seeded rows,
        # so a fresh startup initialization must report "not fresh".
        second = Database(_test_database_url())
        try:
            assert await second.initialize() is False
        finally:
            await second.close()


class TestSeeding:
    async def test_round_trip_matches_file_parsing(self, db, games_config,
                                                   game_parameters_config):
        expected = db_config.loaded_config_from_ini(games_config, game_parameters_config)
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        actual = await db_config.load_config_from_db()
        _assert_same_config(expected, actual)

    async def test_rows_preserve_order_and_values(self, db, games_config,
                                                  game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        # Load each table ordered by insertion id (seed order).
        guilds = await models.Guild.all().order_by("guild_id")
        games = await models.Game.all().order_by("id")
        parameters = await models.GameParameter.all().order_by("id")
        values = await models.ParameterValue.all().order_by("id")
        overrides = await models.GameApiFieldOverride.all()
        default_fields = await models.DefaultApiField.all()

        # The [DEFAULT] section is stored under the sentinel guild id 0.
        assert [guild.guild_id for guild in guilds] == [0, 90401]
        default_games = [game for game in games if game.guild_id == 0]
        # Seeding preserves the config file's game order.
        assert [game.command for game in default_games] == ["game_a", "game_b"]
        game_a = default_games[0]
        assert game_a.name == "Game A"
        assert game_a.role == "<@&111>"
        assert game_a.icon == ""
        assert game_a.default_max_guests == 4
        # Parameters keep their INI order, api fields and value/display pairs.
        game_a_parameters = [parameter for parameter in parameters
                             if parameter.game_id == game_a.id]
        assert [parameter.name for parameter in game_a_parameters] == [
            "param1", "param2", "param3"]
        param1 = game_a_parameters[0]
        assert param1.api_field == "field_one"
        # Config-file parameters default their display name to the name.
        assert param1.display_name == "param1"
        param1_values = [value for value in values if value.parameter_id == param1.id]
        assert [(value.value, value.display_name) for value in param1_values] == [
            ("alpha", "Alpha One"), ("beta", "Beta Two"),
            ("gamma", "Gamma Three"), ("delta", "Delta Four")]
        param3 = game_a_parameters[2]
        assert param3.api_field is None
        game_a_overrides = {override.key: override.field_name
                            for override in overrides if override.game_id == game_a.id}
        assert game_a_overrides == {"api_title_field": "match_title",
                                    "api_table_talk_url_field": "discussion_url",
                                    "api_participants_field": "players",
                                    "api_discord_username_field": "discord_name"}
        # The fixture has no [DEFAULT] section in games_parameters.ini.
        assert default_fields == []

    async def test_loaded_cog_falls_back_to_default(self, db, games_config,
                                                    game_parameters_config, fake_bot):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        loaded = await db_config.load_config_from_db()
        cog = Matchmaking(bot=fake_bot, loaded_config=loaded)
        assert cog.get_guild_config(999999) is cog.default_guild_config
        assert cog.get_guild_config(90401) is cog.guilds[90401]
        assert "game_c" in cog.get_guild_config(90401).games


class TestTokenConfigParsing:
    """The config files name env vars; the token VALUE is stored instead."""

    def _token_cog(self, monkeypatch, token_env_value):
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = {
            "GamesCommands": "game_t",
            "GamesFullNames": "Token Game",
            "GamesRoles": "<@&111>",
            "GamesAPITokenEnvVars": "LFG_TEST_GAME_TOKEN",
        }
        if (token_env_value is not None):
            monkeypatch.setenv("LFG_TEST_GAME_TOKEN", token_env_value)
        else:
            monkeypatch.delenv("LFG_TEST_GAME_TOKEN", raising=False)
        return Matchmaking(bot=FakeBot(), config=parser,
                           game_parameters=configparser.ConfigParser())

    def test_env_var_is_resolved_to_the_token_value(self, monkeypatch):
        cog = self._token_cog(monkeypatch, "resolved-secret")
        option = cog.get_guild_config(42424).games["game_t"]
        assert option.api_token == "resolved-secret"

    def test_unset_env_var_stores_blank_token(self, monkeypatch):
        cog = self._token_cog(monkeypatch, None)
        option = cog.get_guild_config(42424).games["game_t"]
        assert option.api_token == ""


class TestTokenSeeding:
    async def test_seed_stores_token_value_not_env_var(
            self, db, monkeypatch):
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = {
            "GamesCommands": "game_t",
            "GamesFullNames": "Token Game",
            "GamesRoles": "<@&111>",
            "GamesAPITokenEnvVars": "LFG_TEST_GAME_TOKEN",
        }
        monkeypatch.setenv("LFG_TEST_GAME_TOKEN", "seeded-secret")
        await db_config.seed_db_from_config(parser, configparser.ConfigParser())
        game = await models.Game.get_or_none(guild_id=0, command="game_t")
        assert game is not None
        # The resolved token value is persisted; the env-var name is gone
        # (the column was dropped by migration 0003).
        assert game.api_token == "seeded-secret"


class TestTokenMigrationBackfill:
    """Migration 0003: api_token_env_var rows are backfilled from os.environ."""

    async def test_env_vars_are_resolved_during_migration(self, monkeypatch):
        url = os.environ["TEST_DATABASE_URL"]
        database = Database(url)
        try:
            # Rebuild the OLD schema (through 0002: the env-var column still
            # exists, api_token not yet added) from a clean slate.
            await apply_migrations(config=orm_config(url))
            await _drop_all_tables()
            await apply_migrations(config=orm_config(url),
                                   target="models.0002_auto_20260902_1526")
            client = connections.get("default")
            await client.execute_script(
                "INSERT INTO guilds (guild_id) VALUES (42424)")
            await client.execute_script(
                "INSERT INTO games (guild_id, command, name, role, icon, color,"
                " api_token_env_var) VALUES"
                " (42424, 'legacy_game', 'Legacy', '', '', '',"
                " 'LFG_TEST_LEGACY_TOKEN')")
            monkeypatch.setenv("LFG_TEST_LEGACY_TOKEN", "backfilled-secret")
            # Migrate the rest of the way: the backfill resolves the env var.
            await apply_migrations(config=orm_config(url),
                                   target="models.0003_auto_20260902_1615")
            rows = await client.execute_query_dict(
                "SELECT command, api_token FROM games WHERE guild_id = 42424")
            assert rows == [{"command": "legacy_game",
                             "api_token": "backfilled-secret"}]
        finally:
            await database.close()


class TestAdminPersistence:
    """Runtime admin edits: per-guild seeding, adds, updates and deletes."""

    async def test_ensure_guild_config_copies_defaults(self, db, games_config,
                                                       game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        loaded = await db_config.load_config_from_db()
        assert set(loaded.guilds.keys()) == {90401, 42424}
        guild = loaded.guilds[42424]
        # The guild gets its own complete configuration copied from the
        # defaults (the sentinel guild id 0), parameters included.
        assert list(guild.games.keys()) == list(loaded.default_guild_config.games.keys())
        assert "param1" in loaded.game_parameters[42424]["game_a"]

    async def test_add_game_creates_and_duplicate_rejected(self, db, games_config,
                                                           game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.add_game(
            42424, "newgame", name="New Game", role="<@&999>") is True
        assert await db_config.add_game(42424, "newgame", name="Other") is False
        loaded = await db_config.load_config_from_db()
        game = loaded.guilds[42424].games["newgame"]
        assert game.name == "New Game"
        assert game.role == "<@&999>"

    async def test_update_game_changes_existing(self, db, games_config,
                                                game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.update_game(42424, "game_a", name="Renamed") is True
        loaded = await db_config.load_config_from_db()
        assert loaded.guilds[42424].games["game_a"].name == "Renamed"
        assert await db_config.update_game(42424, "missing", name="X") is False

    async def test_delete_game_removes_row(self, db, games_config,
                                           game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.delete_game(42424, "game_a") is True
        loaded = await db_config.load_config_from_db()
        assert "game_a" not in loaded.guilds[42424].games
        assert await db_config.delete_game(42424, "game_a") is False

    async def test_add_parameter_creates_values_and_api_field(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.add_parameter(
            42424, "game_a", "newparam",
            {"one": "One", "two": "Two"}, api_field="new_field",
            display_name="New Param") is True
        loaded = await db_config.load_config_from_db()
        params = loaded.game_parameters[42424]["game_a"]
        assert params["newparam"] == {
            "display_name": "New Param", "values": {"one": "One", "two": "Two"}}
        assert loaded.game_api_fields[42424]["game_a"]["newparam"] == "new_field"

    async def test_add_parameter_without_api_field(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.add_parameter(
            42424, "game_a", "newparam", {"one": "One"}) is True
        loaded = await db_config.load_config_from_db()
        # No api_field: the parameter is Discord-only, not in the field map.
        assert loaded.game_parameters[42424]["game_a"]["newparam"] == {
            "display_name": "newparam", "values": {"one": "One"}}
        assert "newparam" not in loaded.game_api_fields[42424]["game_a"]

    async def test_add_parameter_rejects_duplicate_and_missing_game(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        # Duplicate name on an existing game.
        assert await db_config.add_parameter(
            42424, "game_a", "param1", {"x": "X"}) is False
        # The game is not configured for the guild.
        assert await db_config.add_parameter(
            42424, "missing", "p", {"a": "A"}) is False

    async def test_update_parameter_replaces_values_and_api_field(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.update_parameter(
            42424, "game_a", "param1",
            values={"zed": "Zed"}, api_field="renamed",
            display_name="Renamed Param") is True
        loaded = await db_config.load_config_from_db()
        params = loaded.game_parameters[42424]["game_a"]
        assert params["param1"] == {
            "display_name": "Renamed Param", "values": {"zed": "Zed"}}
        assert loaded.game_api_fields[42424]["game_a"]["param1"] == "renamed"
        # Other parameters of the game are untouched.
        assert "param2" in params

    async def test_update_parameter_keeps_values_when_only_field_changed(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.update_parameter(
            42424, "game_a", "param1", api_field="renamed") is True
        loaded = await db_config.load_config_from_db()
        params = loaded.game_parameters[42424]["game_a"]
        assert params["param1"] == {
            "display_name": "param1",
            "values": {
                "alpha": "Alpha One", "beta": "Beta Two",
                "gamma": "Gamma Three", "delta": "Delta Four"}}
        assert loaded.game_api_fields[42424]["game_a"]["param1"] == "renamed"

    async def test_update_parameter_clears_api_field(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.update_parameter(
            42424, "game_a", "param1", api_field="") is True
        loaded = await db_config.load_config_from_db()
        # An empty api_field clears it: param1 becomes Discord-only.
        assert "param1" not in loaded.game_api_fields[42424]["game_a"]

    async def test_update_parameter_display_name(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.update_parameter(
            42424, "game_a", "param1", display_name="Map Pool") is True
        loaded = await db_config.load_config_from_db()
        params = loaded.game_parameters[42424]["game_a"]
        assert params["param1"]["display_name"] == "Map Pool"
        # Values are untouched.
        assert params["param1"]["values"] == {
            "alpha": "Alpha One", "beta": "Beta Two",
            "gamma": "Gamma Three", "delta": "Delta Four"}
        # An empty display_name resets it to the parameter name.
        assert await db_config.update_parameter(
            42424, "game_a", "param1", display_name="") is True
        loaded = await db_config.load_config_from_db()
        assert loaded.game_parameters[42424]["game_a"]["param1"]["display_name"] == "param1"

    async def test_update_parameter_rejects_missing(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.update_parameter(
            42424, "game_a", "nope", values={"a": "A"}) is False
        assert await db_config.update_parameter(
            42424, "missing", "param1", values={"a": "A"}) is False

    async def test_delete_parameter_cascades_values(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await db_config.ensure_guild_config(42424)
        assert await db_config.delete_parameter(42424, "game_a", "param1") is True
        loaded = await db_config.load_config_from_db()
        assert "param1" not in loaded.game_parameters[42424]["game_a"]
        assert "param1" not in loaded.game_api_fields[42424]["game_a"]
        assert await db_config.delete_parameter(42424, "game_a", "param1") is False

