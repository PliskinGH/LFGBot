"""Tests for the pages that show a server's own configuration."""

import discord

from cogs.matchmaking import db_config
from cogs.matchrolls import db_config as rolls_db_config
from db import config_log, models

from tests.conftest import (ApiChannel, ApiRole, api_client, api_guild,
                            be_operator, csrf_of)

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
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        game = await models.Game.get(guild_id=GAMES_GUILD_ID, command="game_c")
        game.api_token = "s3cret-token"
        game.match_api = "https://example.com/match/"
        await game.save(update_fields=["api_token", "match_api"])
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.get(f"/g/{GAMES_GUILD_ID}")
        # Only whether a token is set is shown, never its value; setting it
        # happens on a page of its own, which the table links to.
        assert "s3cret-token" not in response.text
        assert '<span class="badge text-bg-success">set</span>' in response.text
        assert (f'href="http://testserver/g/90401/games/{game.id}/token"'
                in response.text)
        assert 'type="password"' not in response.text


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

    async def test_the_post_channel_and_the_forum_have_their_own_column(
            self, db, client, login, reads, games_config,
            game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        reads.client = api_client(api_guild(
            GAMES_GUILD_ID, "Server A",
            channels=[ApiChannel(888, "lfg", discord.ChannelType.text),
                      ApiChannel(999, "forum", discord.ChannelType.forum)]))
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        # game_c posts in <#888> and creates its threads in a forum.
        game = await models.Game.get(guild_id=GAMES_GUILD_ID, command="game_c")
        game.forum = "<#999>"
        await game.save(update_fields=["forum"])
        response = client.get(f"/g/{GAMES_GUILD_ID}")
        assert ">Posts in</th>" in response.text
        assert ">Threads in</th>" in response.text
        assert "#lfg" in response.text
        assert "#forum" in response.text

    async def test_an_inherited_game_offers_the_customise_button(
            self, db, client, login, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        # Server B has no configuration of its own: it shows the [DEFAULT]
        # games, each copyable into a configuration of its own.
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        page = client.get(f"/g/{ROLLS_GUILD_ID}").text
        assert 'name="command" value="game_a"' in page
        assert "Customise" in page

    async def test_a_server_that_owns_a_game_edits_it_instead(
            self, db, client, login, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}").text
        # game_c is the server's own: it is linked, not offered as a copy.
        game = await models.Game.get(guild_id=GAMES_GUILD_ID, command="game_c")
        assert f"/g/{GAMES_GUILD_ID}/games/{game.id}" in page
        assert 'name="command" value="game_c"' not in page

    async def test_it_offers_the_defaults_this_server_does_not_have(
            self, db, client, login, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        # Server A owns game_c only: the [DEFAULT] games it never got are
        # listed apart, each with the button that copies it in.
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}").text
        assert "not in this server" in page
        assert 'name="command" value="game_a"' in page
        assert 'name="command" value="game_b"' in page

    async def test_a_server_that_owns_nothing_is_offered_no_reset(
            self, db, client, login, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        login(client, guilds={ROLLS_GUILD_ID: "Server B"})
        page = client.get(f"/g/{ROLLS_GUILD_ID}").text
        assert f"/g/{ROLLS_GUILD_ID}/reset" not in page

    async def test_a_server_owning_a_configuration_may_reset_it(
            self, db, client, login, games_config, game_parameters_config,
            rolls_config, descriptions):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        await rolls_db_config.seed_db_from_config(rolls_config, descriptions)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        assert (f"/g/{GAMES_GUILD_ID}/reset"
                in client.get(f"/g/{GAMES_GUILD_ID}").text)
        response = client.post(f"/g/{GAMES_GUILD_ID}/reset",
                               data={"csrf_token": csrf_of(client)},
                               follow_redirects=False)
        assert response.status_code == 303
        assert await models.Guild.get_or_none(guild_id=GAMES_GUILD_ID) is None
        # Both cogs are told to reload, one change each.
        changes = await config_log.changes_for_guild(GAMES_GUILD_ID)
        assert sorted(change.action for change in changes) == [
            "game.reset", "rollset.reset"]


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

