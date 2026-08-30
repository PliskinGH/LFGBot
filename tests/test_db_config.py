"""Tests for the database-backed configuration (db/ + cogs/matchmaking/db_config.py).

Database tests run against TEST_DATABASE_URL — the production DATABASE_URL is
never used for tests. The test database must be provisioned beforehand
(CI's Postgres service does it); its schema is emptied per test. Tests skip
when TEST_DATABASE_URL is unset or the server is unreachable. The pure
parsing/mapping tests run everywhere.
"""
import os

import pytest
from tortoise import connections
from tortoise.migrations.api.migrate import migrate as apply_migrations

from db import models
from db.db import Database
from db.orm_config import orm_config

from cogs.matchmaking import db_config
from cogs.matchmaking.cog import Matchmaking

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
        # Only games with a games_parameters.ini section appear here.
        assert list(loaded.game_parameters.keys()) == ["game_a"]
        assert loaded.default_api_fields == {}

    def test_parameter_and_api_field_parsing(self, games_config, game_parameters_config):
        loaded = db_config.loaded_config_from_ini(games_config, game_parameters_config)
        params = loaded.game_parameters["game_a"]
        api_fields = loaded.game_api_fields["game_a"]
        # Reserved api_* keys land in game_api_fields, not in the parameters.
        assert api_fields["api_title_field"] == "match_title"
        assert "api_title_field" not in params
        # param1/param2 are submitted to the match API; param3 is Discord-only.
        assert api_fields["param1"] == "field_one"
        assert api_fields["param2"] == "field_two"
        assert "param3" not in api_fields
        assert params["param3"] == {"yes": "yes", "no": "no"}


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

