"""Tests for the pages that show a server's own configuration."""

import discord

from cogs.matchmaking import db_config
from cogs.matchrolls import db_config as rolls_db_config
from db import config_log

from tests.conftest import (ApiChannel, ApiRole, api_client, api_guild,
                            be_operator)

GAMES_GUILD_ID = 90401
ROLLS_GUILD_ID = 42424


class TestGuildPage:
    """The page of one server, for the account that manages it."""

    async def test_it_lists_the_games_of_the_server(self, db, client, login,
                                                    games_config,
                                                    game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.get(f"/g/{GAMES_GUILD_ID}")
        assert response.status_code == 200
        assert "game_c" in response.text
        assert "Game C" in response.text

    async def test_it_lists_the_roll_sets_of_the_server(self, db, client, login,
                                                        rolls_config,
                                                        descriptions):
        await rolls_db_config.seed_db_from_config(rolls_config, descriptions)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        response = client.get(f"/g/{ROLLS_GUILD_ID}")
        assert "Zeta" in response.text

    async def test_it_says_a_server_inherits_the_default(self, db, client, login,
                                                         games_config,
                                                         game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        # This server has no game of its own: it shows the [DEFAULT] ones.
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        response = client.get(f"/g/{ROLLS_GUILD_ID}")
        assert "inherits" in response.text
        assert "game_a" in response.text

    async def test_it_shows_the_recent_changes_of_the_server(self, db, client,
                                                             login):
        await config_log.record_change(
            GAMES_GUILD_ID, actor_id=7, actor_name="Webbie",
            source=config_log.SOURCE_WEB, action="game.add", summary="root")
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.get(f"/g/{GAMES_GUILD_ID}")
        assert "game.add" in response.text
        assert "Webbie" in response.text

    async def test_it_shows_another_servers_changes_nowhere(self, db, client,
                                                            login):
        await config_log.record_change(
            8, actor_id=7, actor_name="Webbie",
            source=config_log.SOURCE_WEB, action="game.add")
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        assert "game.add" not in client.get(f"/g/{GAMES_GUILD_ID}").text

    async def test_it_never_shows_an_api_token(self, db, client, login,
                                               games_config,
                                               game_parameters_config):
        from db import models

        await db_config.seed_db_from_config(games_config, game_parameters_config)
        game = await models.Game.get(guild_id=GAMES_GUILD_ID, command="game_c")
        game.api_token = "s3cret-token"
        game.match_api = "https://example.com/match/"
        await game.save(update_fields=["api_token", "match_api"])
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.get(f"/g/{GAMES_GUILD_ID}")
        # Only whether a token is set is shown, never its value.
        assert "s3cret-token" not in response.text
        assert "token set" in response.text


    async def test_a_server_the_session_does_not_know_is_named_by_discord(
            self, db, client, login, monkeypatch, reads):
        be_operator(monkeypatch)
        reads.client = api_client(api_guild(999, "Named By Discord"))
        login(client)
        response = client.get("/g/999")
        assert response.status_code == 200
        assert "Named By Discord" in response.text

    async def test_the_stored_mentions_are_shown_with_the_names_behind_them(
            self, db, client, login, reads, games_config,
            game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        reads.client = api_client(api_guild(
            GAMES_GUILD_ID, "Server A",
            channels=[ApiChannel(888, "lfg", discord.ChannelType.text)],
            roles=[ApiRole(333, "Raid")]))
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.get(f"/g/{GAMES_GUILD_ID}")
        assert "@Raid" in response.text
        assert "#lfg" in response.text
        # The mentions the configuration stores are replaced, not shown.
        assert "&lt;@&amp;333&gt;" not in response.text
        assert "&lt;#888&gt;" not in response.text


class TestDefaultPage:
    """The operator's view of the ``[DEFAULT]`` configuration."""

    async def test_it_shows_the_default_games(self, db, client, login,
                                              monkeypatch, games_config,
                                              game_parameters_config):
        be_operator(monkeypatch)
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        login(client)
        response = client.get("/ops/default")
        assert response.status_code == 200
        assert "[DEFAULT]" in response.text
        assert "game_a" in response.text
