"""Tests for the game pages: adding, editing, copying and removing games.

Database tests run against TEST_DATABASE_URL, like the other db suites: they
skip when it is unset or the server is unreachable (see the ``db`` fixture).
"""

import discord
import re

from db import config_log, models

from cogs.matchmaking import db_config
from cogs.matchmaking.constants import DEFAULT_GUILD_ID

from tests.conftest import (ApiChannel, ApiMember, ApiRole, ApiTag, api_client,
                            api_guild, be_operator, csrf_of)

# The guilds the config fixtures define (tests/fixtures/games.ini).
GAMES_GUILD_ID = 90401
OTHER_GUILD_ID = 42424


async def _seed(games_config, game_parameters_config) -> None:
    """The configuration the fixtures describe, in the test database."""
    await db_config.seed_db_from_config(games_config, game_parameters_config)


async def _game(guild_id: int = GAMES_GUILD_ID, command: str = "game_c"):
    """A stored game row."""
    return await models.Game.get_or_none(guild_id=guild_id, command=command)


async def _game_id(command: str = "game_c",
                   guild_id: int = GAMES_GUILD_ID) -> int:
    """The row id of a game, as the pages address it."""
    return (await _game(guild_id, command)).id


async def _parameter_id(name: str) -> int:
    """The row id of a parameter of the seeded game, as the pages address it."""
    return (await models.GameParameter.get(game_id=await _game_id(),
                                           name=name)).id


async def _changes() -> list[models.ConfigChange]:
    """Every logged change, newest first."""
    return await models.ConfigChange.all().order_by("-id")


def _post(client, path: str, data: dict, follow_redirects: bool = False):
    """Post a game form with the session's CSRF token, as the pages require."""
    return client.post(path, data=dict(data, csrf_token=csrf_of(client)),
                       follow_redirects=follow_redirects)


def _form(**overrides) -> dict:
    """A complete game form: the pages post every field, always.

    A posted colour ticks its box, unless the caller says otherwise: the box is
    what makes the picker's value count.
    """
    data = {"command": "", "name": "", "role": "", "channel": "", "forum": "",
            "tag": "", "visibility": "", "icon": "", "color": "", "message": "",
            "registration_api": "", "match_api": "", "match_url": "",
            "website_url": "", "registration_url": "", "profile_url": "",
            "max_players": "", "title_field": "", "table_talk_url_field": "",
            "participants_field": "", "discord_username_field": ""}
    data.update(overrides)
    if (data.get("color")):
        data.setdefault("use_color", "1")
    return data


class TestNewGame:
    """Adding a game from the panel."""

    async def test_it_adds_a_game_and_queues_one_change(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
                         _form(command="root", name="Root night"))
        assert response.status_code == 303, response.text
        game = await _game(command="root")
        assert (game.name, game.default_max_guests) == ("Root night", None)
        change, = await _changes()
        assert (change.action, change.summary, change.source,
                change.guild_id) == ("game.add", "root",
                                     config_log.SOURCE_WEB, GAMES_GUILD_ID)
        assert change.applied_at is None  # the bot still owes it
        assert (change.actor_id, change.actor_name) == (42, "Manager")

    async def test_the_colour_picker_value_is_stored_as_a_decimal(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="root", color="#ff0000"))
        assert (await _game(command="root")).color == "16711680"

    async def test_a_malformed_colour_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
                         _form(command="root", color="red"))
        assert response.status_code == 200
        assert "must be a colour like #1a2b3c" in response.text
        assert await _game(command="root") is None
        assert await _changes() == []

    async def test_a_bad_command_name_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
                         _form(command="Not Valid", name="Root"))
        assert "is not a valid slash command name" in response.text
        assert await _game(command="Not Valid") is None

    async def test_a_command_that_is_already_configured_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
                         _form(command="game_c", name="Twin"))
        assert "is already configured here" in response.text
        assert (await _game()).name == "Game C"

    async def test_max_players_is_stored_as_the_guest_count(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="root", max_players="5"))
        assert (await _game(command="root")).default_max_guests == 4

    async def test_an_out_of_range_player_count_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
                         _form(command="root", max_players="101"))
        assert "`max_players` must be between 2 and 100" in response.text

    async def test_a_visibility_the_cog_cannot_read_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        # The select offers public and private; a crafted post must not store
        # a value the cog would fail to read when it creates a thread.
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
                         _form(command="root", visibility="private"))
        assert "`visibility` must be a number" in response.text
        assert await _game(command="root") is None

    async def test_the_api_overrides_are_stored(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="root", title_field="match_title"))
        game = await _game(command="root")
        override = await models.GameApiFieldOverride.get(game=game)
        assert (override.key, override.field_name) == (
            "api_title_field", "match_title")

    async def test_the_form_offers_the_discord_choices(
            self, db, client, login, reads, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        forum = ApiChannel(999, "forum", discord.ChannelType.forum,
                           [ApiTag(30, "Game")])
        reads.client = api_client(
            api_guild(GAMES_GUILD_ID, "Server A",
                      channels=[ApiChannel(888, "lfg",
                                          discord.ChannelType.text), forum],
                      roles=[ApiRole(333, "Raid")]),
            forums=[forum])
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}/games/new").text
        assert '<option value="&lt;#888&gt;"' in page
        assert '<option value="Game"' in page  # a tag is picked by its name
        assert "#forum" in page
        assert 'name="role"' not in page  # the ping is edited on its own page

    async def test_without_the_csrf_token_it_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.post(f"/g/{GAMES_GUILD_ID}/games/new",
                               data=_form(command="root"))
        assert response.status_code == 403
        assert await _game(command="root") is None

    async def test_a_manager_of_another_server_cannot_reach_it(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        response = client.get(f"/g/{GAMES_GUILD_ID}/games/new")
        assert response.status_code == 404


class TestEditGame:
    """Editing a game: the form posts every field, so an emptied one clears it."""

    async def test_it_shows_the_stored_values(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}").text
        assert 'value="Game C"' in page
        # The ping is not on this form: it has its own page.
        assert 'name="role"' not in page
        assert "The ping and the API token are set on" in page
        assert "Parameters" in page

    async def test_it_clears_what_was_emptied(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}",
                         _form(command="game_c"))
        assert response.status_code == 303
        game = await _game()
        assert (game.name, game.channel) == ("", None)
        assert game.default_max_guests is None
        # The ping is no longer part of this form, so saving must leave it alone.
        assert game.role == "<@&333>"
        change, = await _changes()
        assert (change.action, change.summary) == (
            "game.update", "channel, default_max_guests, name")

    async def test_a_rename_moves_the_game(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}",
              _form(command="game_renamed", name="Game C"))
        assert await _game() is None
        assert (await _game(command="game_renamed")).name == "Game C"
        change, = await _changes()
        assert (change.action, change.summary) == (
            "game.rename", "game_c -> game_renamed")

    async def test_a_rename_onto_a_configured_game_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new", _form(command="root"))
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}",
                         _form(command="root"))
        assert "is already configured for this server" in response.text
        assert (await _game()).command == "game_c"
        assert [change.action for change in await _changes()] == ["game.add"]

    async def test_a_form_with_no_change_logs_nothing(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}",
                         _form(command="game_c", name="Game C",
                               role="<@&333>", channel="<#888>",
                               max_players="4"),
                         follow_redirects=True)
        assert "Nothing changed in `game_c`" in response.text
        assert await _changes() == []

    async def test_an_unknown_game_is_not_found(self, db, client, login):
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        assert client.get(f"/g/{GAMES_GUILD_ID}/games/999999").status_code == 404
        assert client.get(
            f"/g/{GAMES_GUILD_ID}/games/999999/remove").status_code == 404

    async def test_it_copies_a_game(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/copy",
              {"command": "game_twin", "name": "Twin"})
        twin = await _game(command="game_twin")
        assert (twin.name, twin.role) == ("Twin", "<@&333>")
        change, = await _changes()
        assert (change.action, change.summary) == (
            "game.copy", "game_twin from game_c")

    async def test_removing_a_game_needs_the_confirmation_page(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        confirm = client.get(f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/remove")
        assert confirm.status_code == 200
        assert "cannot be undone" in confirm.text
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/remove", {})
        assert await _game() is None
        change, = await _changes()
        assert (change.action, change.summary) == ("game.remove", "game_c")


class TestParameterPages:
    """A game's parameters: its accepted values, its label, its API field."""

    async def test_it_adds_a_parameter(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/parameters/new",
              {"name": "style", "display_name": "Style",
               "values": "(a, Alpha), (b, Bravo)", "api_field": "match_style"})
        parameter = await models.GameParameter.get(name="style")
        assert (parameter.display_name, parameter.api_field) == (
            "Style", "match_style")
        values = await models.ParameterValue.filter(
            parameter=parameter).order_by("id")
        assert [value.value for value in values] == ["a", "b"]
        change, = await _changes()
        assert (change.action, change.summary) == (
            "parameter.add", "game_c/style")

    async def test_a_bad_parameter_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(
            client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/parameters/new",
            {"name": "style", "display_name": "", "values": "", "api_field": ""})
        assert "must contain at least one value" in response.text
        assert await models.GameParameter.filter(name="style").count() == 0

    async def test_it_edits_a_parameter(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/parameters/new",
              {"name": "style", "display_name": "", "values": "a",
               "api_field": ""})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/parameters/{await _parameter_id('style')}",
              {"name": "style", "display_name": "Style", "values": "a",
               "api_field": "match_style"})
        parameter = await models.GameParameter.get(name="style")
        assert (parameter.display_name, parameter.api_field) == (
            "Style", "match_style")
        assert await models.ParameterValue.filter(
            parameter=parameter).count() == 1
        change = (await _changes())[0]
        assert (change.action, change.summary) == (
            "parameter.update", "game_c/style")

    async def test_it_removes_a_parameter(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/parameters/new",
              {"name": "style", "display_name": "", "values": "a",
               "api_field": ""})
        _post(client,
              f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/parameters/{await _parameter_id('style')}/remove", {})
        assert await models.GameParameter.filter(name="style").count() == 0
        change = (await _changes())[0]
        assert (change.action, change.summary) == (
            "parameter.remove", "game_c/style")


class TestPingPages:
    """The ping: a role select, and a member by name or by mention."""

    async def test_the_page_shows_the_roles_and_the_current_ping(
            self, db, client, login, reads, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        reads.client = api_client(api_guild(GAMES_GUILD_ID, "Server A",
                                           roles=[ApiRole(333, "Raid")]))
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping").text
        assert '<option value="&lt;@&amp;333&gt;"' in page
        assert "@Raid" in page  # the ping the game has now, resolved
        assert f"/games/{await _game_id()}/ping/clear" in page

    async def test_a_role_from_the_select_is_stored(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping",
                         {"role": "<@&111>", "member": ""})
        assert response.status_code == 303
        assert (await _game()).role == "<@&111>"
        change, = await _changes()
        assert (change.action, change.summary) == ("game.ping", "game_c")

    async def test_a_typed_name_is_resolved_to_a_member(
            self, db, client, login, reads, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        reads.client = api_client(api_guild(
            GAMES_GUILD_ID, "Server A",
            members=[ApiMember(42, "hosty", nick="Hosty")]))
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping",
              {"role": "", "member": "Hosty"})
        assert (await _game()).role == "<@42>"

    async def test_a_pasted_mention_is_stored_as_it_stands(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping",
              {"role": "", "member": "<@!42>"})
        assert (await _game()).role == "<@!42>"

    async def test_a_name_nobody_matches_is_refused(
            self, db, client, login, reads, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        reads.client = api_client(api_guild(GAMES_GUILD_ID, "Server A"))
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping",
                         {"role": "", "member": "Nobody"})
        assert response.status_code == 200
        assert "No member of this server matches `Nobody`" in response.text
        assert (await _game()).role == "<@&333>"
        assert await _changes() == []

    async def test_a_role_and_a_member_together_are_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping",
                         {"role": "<@&111>", "member": "<@42>"})
        assert "Give either a role or a member, not both." in response.text
        assert (await _game()).role == "<@&333>"

    async def test_an_empty_ping_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping",
                         {"role": "", "member": ""})
        assert "Give a role or a member" in response.text
        assert await _changes() == []

    async def test_it_clears_the_ping(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping/clear", {})
        assert (await _game()).role == ""
        change, = await _changes()
        assert (change.action, change.summary) == ("game.ping.clear", "game_c")

    async def test_without_csrf_the_ping_is_not_touched(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.post(f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/ping",
                               data={"role": "<@&111>", "member": ""})
        assert response.status_code == 403
        assert (await _game()).role == "<@&333>"

    async def test_the_server_page_offers_the_ping_button(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}").text
        assert f"/games/{await _game_id()}/ping" in page
        assert '<span class="badge text-bg-secondary">not set</span>' in page
        # The league/API columns are not badges in the table any more.
        assert ">league<" not in page
        assert 'btn btn-sm btn-outline-secondary' in page
        assert 'btn btn-sm btn-outline-danger' in page


class TestApiToken:
    """The token has its own two posts, and is never rendered."""

    async def test_it_sets_a_token_and_never_shows_it(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token",
                         {"api_token": "s3cret-token"}, follow_redirects=True)
        assert (await _game()).api_token == "s3cret-token"
        assert "s3cret-token" not in response.text
        change, = await _changes()
        assert (change.action, change.summary) == ("game.token", "game_c")
        assert "s3cret-token" not in change.summary

    async def test_it_clears_a_token(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token",
              {"api_token": "s3cret-token"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token/clear", {})
        assert (await _game()).api_token == ""
        change = (await _changes())[0]
        assert (change.action, change.summary) == ("game.token.clear", "game_c")

    async def test_an_empty_token_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token",
                         {"api_token": ""})
        assert response.status_code == 200
        assert "Give a token, or use the Clear button" in response.text
        assert await _changes() == []

    async def test_the_token_page_shows_no_value_and_no_clear_button(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token").text
        assert 'type="password"' in page
        assert "No token is set for this game." in page
        assert f"/games/{await _game_id()}/token/clear" not in page

    async def test_the_token_page_offers_to_clear_a_set_token(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token",
              {"api_token": "s3cret-token"})
        page = client.get(f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token").text
        assert "A token is set for this game" in page
        assert f"/games/{await _game_id()}/token/clear" in page
        assert "s3cret-token" not in page

    async def test_the_server_page_links_to_the_token_page(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}").text
        assert '<span class="badge text-bg-secondary">not set</span>' in page
        assert f'/games/{await _game_id()}/token"' in page
        assert 'type="password"' not in page

    async def test_setting_a_token_without_csrf_is_refused(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        response = client.post(f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}/token",
                               data={"api_token": "s3cret-token"})
        assert response.status_code == 403
        assert (await _game()).api_token == ""


class TestDefaultConfiguration:
    """The [DEFAULT] config: the same pages, for the panel's operators."""

    async def test_an_operator_edits_the_default_configuration(
            self, db, client, login, monkeypatch, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        be_operator(monkeypatch)
        login(client)
        response = _post(client, "/ops/default/games/new",
                         _form(command="root", name="Root"))
        assert response.status_code == 303
        assert (await _game(guild_id=0, command="root")).name == "Root"
        change, = await _changes()
        assert (change.action, change.guild_id) == ("game.add", 0)

    async def test_a_manager_cannot_edit_the_default_configuration(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        assert client.get("/ops/default/games/new").status_code == 403
        assert client.get("/ops/default/games/999999").status_code == 403


class TestACommandCalledNew:
    """A game whose command is `new` is addressed by its id, like any other.

    Its edit page must not be the add-game page, and its parameters must not be
    taken for the add-parameter page either.
    """

    async def test_it_edits_the_game_instead_of_adding_one(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="new", name="New"))
        page = client.get(
            f"/g/{GAMES_GUILD_ID}/games/{await _game_id('new')}").text
        assert "Edit new" in page
        assert "Add a game" not in page


class TestColourPicker:
    """A colour input always posts a value, so its box decides what it means."""

    async def test_an_untouched_picker_stores_no_colour(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        # A browser posts its white default whether or not anyone picked it.
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="root", color="#ffffff", use_color=""))
        assert (await _game(command="root")).color == ""

    async def test_unticking_the_box_clears_a_stored_colour(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="root", color="#ff0000"))
        assert (await _game(command="root")).color == "16711680"
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id('root')}",
              _form(command="root", color="#ffffff", use_color=""))
        assert (await _game(command="root")).color == ""

    async def test_the_box_reflects_what_is_stored(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}/games/new").text
        assert 'name="use_color"' in page
        assert "checked" not in page  # the colour box is the only checkbox
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="root", color="#ff0000"))
        page = client.get(
            f"/g/{GAMES_GUILD_ID}/games/{await _game_id('root')}").text
        assert 'value="#ff0000"' in page
        assert "checked" in page

    async def test_a_stored_colour_survives_an_untouched_save(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="root", color="#ff0000"))
        changes = len(await _changes())
        # The picker sends back the colour it was shown: nothing to write, and
        # nothing for the bot to apply.
        response = _post(client, f"/g/{GAMES_GUILD_ID}/games/{await _game_id('root')}",
                         _form(command="root", color="#ff0000"))
        assert response.status_code == 303
        assert (await _game(command="root")).color == "16711680"
        assert len(await _changes()) == changes


class TestTheDefaultPlayersField:
    """The default maximum is a number field, bounded like the option is."""

    async def test_the_form_bounds_the_number_it_takes(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(f"/g/{GAMES_GUILD_ID}/games/new").text
        assert "Default maximum players" in page
        assert 'type="number"' in page
        assert 'min="2"' in page and 'max="100"' in page

    async def test_it_shows_the_stored_maximum(
            self, db, client, login, games_config, game_parameters_config):
        # The fixture gives game_c four players, stored as three guests.
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = client.get(
            f"/g/{GAMES_GUILD_ID}/games/{await _game_id()}").text
        assert 'value="4"' in page


class TestAdoptingAnInheritedGame:
    """Customising an inherited game gives the server its own copy of it."""

    async def test_it_copies_the_configuration_and_opens_the_game(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        response = _post(client, f"/g/{OTHER_GUILD_ID}/games/adopt",
                         {"command": "game_a"})
        assert response.status_code == 303, response.text
        game = await _game(command="game_a", guild_id=OTHER_GUILD_ID)
        assert game is not None
        # The manager lands on the server's own copy of that game.
        assert response.headers["location"].endswith(f"/games/{game.id}")
        # The whole [DEFAULT] configuration came along, and the change is logged.
        assert await _game(command="game_b", guild_id=OTHER_GUILD_ID) is not None
        change, = await _changes()
        assert (change.action, change.summary) == ("game.adopt", "game_a")
        assert change.guild_id == OTHER_GUILD_ID

    async def test_it_adopts_a_game_the_server_predates(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        # Server B already owns a configuration that lacks one game: only that
        # game is copied, without touching the rest.
        await db_config.ensure_guild_config(OTHER_GUILD_ID)
        await models.Game.filter(guild_id=OTHER_GUILD_ID,
                                 command="game_a").delete()
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        response = _post(client, f"/g/{OTHER_GUILD_ID}/games/adopt",
                         {"command": "game_a"})
        assert response.status_code == 303, response.text
        assert await _game(command="game_a", guild_id=OTHER_GUILD_ID) is not None

    async def test_a_command_nobody_has_materializes_nothing(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        response = _post(client, f"/g/{OTHER_GUILD_ID}/games/adopt",
                         {"command": "nope"})
        assert response.status_code == 404
        assert await models.Guild.get_or_none(guild_id=OTHER_GUILD_ID) is None
        assert await _changes() == []

    async def test_without_csrf_nothing_is_copied(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        response = client.post(f"/g/{OTHER_GUILD_ID}/games/adopt",
                               data={"command": "game_a"})
        assert response.status_code == 403
        assert await models.Guild.get_or_none(guild_id=OTHER_GUILD_ID) is None


def _cancel_of(page: str) -> str:
    """The URL a confirmation page's Cancel button points at."""
    match = re.search(r'href="([^"]+)"[^>]*>Cancel</a>', page)
    assert match is not None, "the page has no Cancel button"
    return match.group(1)


def _remove_link_of(page: str, game_id: int) -> str:
    """The URL a page's Remove link for a game points at."""
    match = re.search(rf'href="([^"]*games/{game_id}/remove[^"]*)"', page)
    assert match is not None, "the page offers no Remove link for the game"
    return match.group(1)


class TestCancelLinks:
    """Cancel leaves the page it is on: an empty href reloads it, and a form
    cancels back to the games table its actions belong to."""

    async def test_removing_a_game_from_the_table_cancels_to_the_table(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        game_id = await _game_id()
        link = _remove_link_of(
            client.get(f"/g/{GAMES_GUILD_ID}").text, game_id)
        cancel = _cancel_of(client.get(link).text)
        assert cancel.endswith(f"/g/{GAMES_GUILD_ID}")
        assert not cancel.endswith(f"/g/{GAMES_GUILD_ID}/games/{game_id}")

    async def test_removing_a_game_from_its_page_cancels_to_the_table_too(
            self, db, client, login, games_config, game_parameters_config):
        # Whichever link was followed, Cancel goes back to the games table.
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        game_id = await _game_id()
        page = f"/g/{GAMES_GUILD_ID}/games/{game_id}"
        link = _remove_link_of(client.get(page).text, game_id)
        cancel = _cancel_of(client.get(link).text)
        assert cancel.endswith(f"/g/{GAMES_GUILD_ID}")
        assert not cancel.endswith(page)

    async def test_removing_a_parameter_cancels_to_the_game(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        game_id = await _game_id()
        # game_c starts without parameters: add one to have a page to open.
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{game_id}/parameters/new",
              {"name": "style", "display_name": "", "values": "a", "api_field": ""})
        parameter_id = await _parameter_id("style")
        page = (f"/g/{GAMES_GUILD_ID}/games/{game_id}"
                f"/parameters/{parameter_id}/remove")
        cancel = _cancel_of(client.get(page).text)
        assert cancel.endswith(f"/g/{GAMES_GUILD_ID}/games/{game_id}")
        assert not cancel.endswith(page)

    async def test_removing_the_configuration_cancels_to_the_server(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        page = f"/g/{GAMES_GUILD_ID}/reset"
        cancel = _cancel_of(client.get(page).text)
        assert cancel.endswith(f"/g/{GAMES_GUILD_ID}")
        assert not cancel.endswith(page)

    async def test_the_ping_and_token_pages_cancel_to_the_games_table(
            self, db, client, login, games_config, game_parameters_config):
        # Setting a ping or a token is a server-level action, offered from the
        # games table: cancelling one goes back there, not to the game's form.
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        game_id = await _game_id()
        for name in ("ping", "token"):
            page = f"/g/{GAMES_GUILD_ID}/games/{game_id}/{name}"
            cancel = _cancel_of(client.get(page).text)
            assert cancel.endswith(f"/g/{GAMES_GUILD_ID}")
            assert not cancel.endswith(f"/g/{GAMES_GUILD_ID}/games/{game_id}")


class TestInheritedGames:
    """A game the server does not own is shown, but not editable from here."""

    async def test_the_row_offers_no_action(
            self, db, client, login, games_config, game_parameters_config):
        # Guild 42424 has no games of its own: it shows [DEFAULT]'s, which have
        # no rows here and so no pages to link to.
        await _seed(games_config, game_parameters_config)
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        page = client.get(f"/g/{OTHER_GUILD_ID}").text
        assert "game_a" in page
        assert "inherited" in page
        assert ">Edit</a>" not in page
        assert f"/g/{OTHER_GUILD_ID}/games/game_a" not in page

    async def test_it_removes_it_and_takes_its_parameters(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        login(client, guilds={GAMES_GUILD_ID: "Server A"})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/new",
              _form(command="new", name="New"))
        game_id = await _game_id("new")
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{game_id}/parameters/new",
              {"name": "new", "display_name": "", "values": "a", "api_field": ""})
        _post(client, f"/g/{GAMES_GUILD_ID}/games/{game_id}/remove", {})
        assert await _game(command="new") is None
        assert await models.GameParameter.filter(name="new").count() == 0


class TestAnUnconfiguredServer:
    """A server with no configuration of its own can be configured here."""

    async def test_adding_a_game_gives_it_its_own_configuration(
            self, db, client, login, games_config, game_parameters_config):
        await _seed(games_config, game_parameters_config)
        # Server B is in neither games.ini nor any earlier edit: it inherits
        # [DEFAULT] until its first write, which copies the defaults into rows
        # of its own, exactly as /games add does.
        assert await models.Guild.get_or_none(guild_id=OTHER_GUILD_ID) is None
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        response = _post(client, f"/g/{OTHER_GUILD_ID}/games/new",
                         _form(command="root", name="Root"))
        assert response.status_code == 303, response.text
        assert await models.Guild.get_or_none(guild_id=OTHER_GUILD_ID) is not None
        assert (await _game(command="root", guild_id=OTHER_GUILD_ID)).name == "Root"
        # The inherited defaults came along, so the server now owns them.
        assert await _game(command="game_a", guild_id=OTHER_GUILD_ID) is not None
        change, = await _changes()
        assert change.guild_id == OTHER_GUILD_ID

    async def test_editing_creates_nothing(
            self, db, client, login, games_config, game_parameters_config):
        # An edit addresses a row that exists already: with none to address,
        # the panel answers not-found and the server keeps inheriting.
        await _seed(games_config, game_parameters_config)
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        response = _post(client, f"/g/{OTHER_GUILD_ID}/games/999999",
                         _form(command="root", name="Root"))
        assert response.status_code == 404
        assert await models.Guild.get_or_none(guild_id=OTHER_GUILD_ID) is None
        assert await _changes() == []

    async def test_another_servers_game_cannot_be_edited(
            self, db, client, login, games_config, game_parameters_config):
        # Server A owns game_c; a manager of Server B must not reach it by id.
        await _seed(games_config, game_parameters_config)
        game_id = await _game_id()
        login(client, guilds={OTHER_GUILD_ID: "Server B"})
        assert client.get(
            f"/g/{OTHER_GUILD_ID}/games/{game_id}").status_code == 404
        response = _post(client, f"/g/{OTHER_GUILD_ID}/games/{game_id}",
                         _form(command="game_c", name="Hijacked"))
        assert response.status_code == 404
        assert (await _game()).name == "Game C"


class TestShowingParameterValues:
    """A parameter's accepted values are shown as the text they are given in.

    The row keys them ``values_text``: a key called ``values`` would find the
    dict method of that name and render ``<built-in method values of dict
    object at 0x...>`` in the page instead of the values.
    """

    async def _default_game_a(self):
        """game_a's [DEFAULT] row, the one the fixtures give parameters."""
        return await _game(command="game_a", guild_id=DEFAULT_GUILD_ID)

    async def test_the_parameter_table_shows_them(
            self, db, client, login, monkeypatch, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        be_operator(monkeypatch)
        login(client)
        game = await self._default_game_a()
        page = client.get(f"/ops/default/games/{game.id}").text
        assert "Alpha One" in page
        assert "built-in method" not in page
        # The row's actions are buttons, as in the server's games table.
        assert 'class="btn btn-sm btn-outline-secondary"' in page
        assert 'class="btn btn-sm btn-outline-danger"' in page

    async def test_the_parameter_form_shows_them(
            self, db, client, login, monkeypatch, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        be_operator(monkeypatch)
        login(client)
        game = await self._default_game_a()
        parameter = await models.GameParameter.get(game_id=game.id,
                                                   name="param1")
        page = client.get(
            f"/ops/default/games/{game.id}/parameters/{parameter.id}").text
        assert "Alpha One" in page
        assert "built-in method" not in page

    async def test_an_invalid_edit_keeps_them_and_shows_the_error(
            self, db, client, login, monkeypatch, games_config,
            game_parameters_config):
        await _seed(games_config, game_parameters_config)
        be_operator(monkeypatch)
        login(client)
        game = await self._default_game_a()
        parameter = await models.GameParameter.get(game_id=game.id,
                                                   name="param1")
        path = f"/ops/default/games/{game.id}/parameters/{parameter.id}"
        response = client.post(path, data={
            "csrf_token": csrf_of(client, path), "name": "param1",
            "display_name": "", "values": "", "api_field": ""})
        # The form comes back with the error rather than a 500: a rejected edit
        # re-renders the parameter it edits, so the page has to name that row.
        assert response.status_code == 200
        assert "must contain at least one value" in response.text
        assert "built-in method" not in response.text

