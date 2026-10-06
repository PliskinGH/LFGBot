"""Tests for the operator pages: every server, and the change log."""

from datetime import timedelta

from tortoise import timezone

from db import config_log, models

from tests.conftest import api_client, api_guild, be_operator, csrf_of


async def _change(guild_id: int = 7, action: str = "game.add",
                  source: str = config_log.SOURCE_WEB, applied: bool = False,
                  days_ago: int = 0, actor_name: str = "Webbie"):
    """Log one change, stamped as applied a while ago when asked to be."""
    change = await config_log.record_change(
        guild_id, actor_id=1, actor_name=actor_name, source=source,
        action=action, applied=applied)
    if (days_ago):
        change.applied_at = timezone.now() - timedelta(days=days_ago)
        await change.save(update_fields=["applied_at"])
    return change


class TestHub:
    """The page listing every server the bot is in."""

    async def test_it_lists_the_servers_the_bot_is_in(self, db, client, login,
                                                      monkeypatch, reads):
        be_operator(monkeypatch)
        reads.client = api_client(api_guild(7, "Bot Server"))
        login(client)
        response = client.get("/ops")
        assert response.status_code == 200
        assert "Bot Server" in response.text

    async def test_it_marks_a_server_that_has_its_own_configuration(
            self, db, client, login, monkeypatch, games_config,
            game_parameters_config):
        from cogs.matchmaking import db_config

        be_operator(monkeypatch)
        await db_config.seed_db_from_config(games_config,
                                            game_parameters_config)
        login(client)
        response = client.get("/ops")
        assert "own" in response.text

    async def test_a_server_the_bot_is_not_in_is_marked_not_repeated(
            self, db, client, login, monkeypatch, reads, games_config,
            game_parameters_config):
        from cogs.matchmaking import db_config

        be_operator(monkeypatch)
        await db_config.seed_db_from_config(games_config,
                                            game_parameters_config)
        # A logged-in client that knows no server: Discord was read, this
        # server is not one the bot is in.
        reads.client = api_client()
        login(client)
        response = client.get("/ops")
        assert "the bot is not in this server" in response.text
        # The Server column no longer stands in the id of the Id column.
        assert "<td>90401</td>" not in response.text
        assert response.text.count("<code>90401</code>") == 1

    async def test_a_server_without_a_readable_discord_says_why(
            self, db, client, login, monkeypatch, reads, games_config,
            game_parameters_config):
        from cogs.matchmaking import db_config

        be_operator(monkeypatch)
        await db_config.seed_db_from_config(games_config,
                                            game_parameters_config)
        reads.client = None  # No client logged in: Discord cannot be read.
        login(client)
        response = client.get("/ops")
        assert "Discord could not be read" in response.text
        assert "<td>90401</td>" not in response.text
        assert response.text.count("<code>90401</code>") == 1


class TestChangeLog:
    """The log page, and the filters it answers."""

    async def test_it_lists_the_changes_of_every_server(self, db, client, login,
                                                        monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="game.add")
        await _change(guild_id=8, action="rollset.category.add")
        login(client)
        response = client.get("/ops/changes")
        assert "game.add" in response.text
        assert "rollset.category.add" in response.text

    async def test_it_filters_by_server(self, db, client, login, monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="game.add")
        await _change(guild_id=8, action="rollset.category.add")
        login(client)
        response = client.get("/ops/changes", params={"guild_id": 7})
        assert "game.add" in response.text
        assert "rollset.category.add" not in response.text

    async def test_it_filters_by_state(self, db, client, login, monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="waiting.action")
        await _change(guild_id=7, action="done.action", applied=True)
        login(client)
        response = client.get("/ops/changes", params={"state": "pending"})
        assert "waiting.action" in response.text
        assert "done.action" not in response.text

    async def test_it_filters_by_source(self, db, client, login, monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="from.discord",
                      source=config_log.SOURCE_DISCORD, applied=True)
        await _change(guild_id=7, action="from.web")
        login(client)
        response = client.get("/ops/changes", params={"source": "web"})
        assert "from.web" in response.text
        assert "from.discord" not in response.text


class TestPrune:
    """Deleting the applied entries, and never a pending one."""

    async def test_the_page_counts_what_it_would_delete(self, db, client, login,
                                                        monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="old.action", applied=True,
                      days_ago=100)
        await _change(guild_id=7, action="recent.action", applied=True)
        await _change(guild_id=7, action="waiting.action")
        login(client)
        response = client.get("/ops/changes/prune")
        assert response.status_code == 200
        assert "1 applied entry/entries are older than 90 day(s)" in response.text

    async def test_pruning_deletes_only_the_applied_entries(self, db, client,
                                                            login, monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="old.action", applied=True,
                      days_ago=100)
        await _change(guild_id=7, action="waiting.action")
        login(client)
        response = client.post(
            "/ops/changes/prune",
            data={"csrf_token": csrf_of(client, "/ops/changes/prune"),
                  "days": 90},
            follow_redirects=False)
        assert response.status_code == 303
        left = await models.ConfigChange.all()
        assert [change.action for change in left] == ["waiting.action"]

    async def test_pruning_needs_the_session_token(self, db, client, login,
                                                   monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="old.action", applied=True,
                      days_ago=100)
        login(client)
        assert client.post("/ops/changes/prune",
                           data={"days": 90}).status_code == 403
        assert await models.ConfigChange.all().count() == 1

    async def test_a_useless_number_of_days_is_refused(self, db, client, login,
                                                       monkeypatch):
        be_operator(monkeypatch)
        await _change(guild_id=7, action="old.action", applied=True,
                      days_ago=100)
        login(client)
        response = client.post(
            "/ops/changes/prune",
            data={"csrf_token": csrf_of(client, "/ops/changes/prune"),
                  "days": "0"})
        assert "at least 1" in response.text
        assert await models.ConfigChange.all().count() == 1
