"""Tests for the ``cogs/matchmaking.py`` cog."""
import asyncio
import aiohttp
import configparser
import inspect
from types import SimpleNamespace

import discord
import pytest

from common import constants
from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.constants import GAMES_COMMAND, LFG_COMMAND, RENAME_COMMAND
from cogs.matchmaking.models import GameOption, LFGContext
from cogs.matchmaking.views import GameSettingsModal, ThreadRenameModal

from tests.conftest import (
    FakeBot,
    FakeChannel,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakeMessage,
    FakeMentionable,
)


def _run(coro):
    """Run an async coroutine synchronously for non-async test bodies."""
    return asyncio.run(coro)


class TestParseDefaultMaxGuests:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("5", 4),
            ("2", 1),
            ("100", 99),
            (None, None),
            ("", None),
            ("1", None),     # below the 2-100 range
            ("101", None),   # above the 2-100 range
            ("abc", None),   # not a number
        ],
    )
    def test_parsing(self, raw, expected):
        assert Matchmaking.parse_default_max_guests(raw) == expected


class TestConfigLoading:
    def test_default_section_loads_all_games(self, matchmaking):
        games = matchmaking.default_guild_config.games
        assert set(games.keys()) == {"game_a", "game_b"}

        game_a = games["game_a"]
        assert game_a.name == "Game A"
        assert game_a.role == "<@&111>"
        assert game_a.icon == ""  # missing icon falls back to default at render time
        assert game_a.default_max_guests == 4
        assert game_a.message == ""  # empty per config; index 1 holds the message

        game_b = games["game_b"]
        assert game_b.name == "Game B"
        assert game_b.icon == "https://example.com/icon.png"
        # Game B inherits GamesMaxPlayers=2 from DEFAULT -> default max guests = 1.
        assert game_b.default_max_guests == 1
        assert game_b.message == "Please check the rules."

    def test_guild_specific_section_overrides_default(self, matchmaking):
        guild_config = matchmaking.guilds[90401]
        assert guild_config.guild_id == 90401
        game_c = guild_config.games["game_c"]
        assert game_c.name == "Game C"
        assert game_c.role == "<@&333>"
        assert game_c.default_max_guests == 3

    def test_section_without_id_is_ignored(self, matchmaking):
        # games.ini contains a [NoID] section with no ID value; it must be skipped.
        assert len(matchmaking.guilds) == 1
        assert 90401 in matchmaking.guilds

    def test_get_guild_config_falls_back_to_default(self, matchmaking):
        default = matchmaking.get_guild_config(1)
        assert default is matchmaking.default_guild_config
        assert default.guild_id is None

        guild_config = matchmaking.get_guild_config(90401)
        assert guild_config is matchmaking.guilds[90401]
        assert "game_c" in guild_config.games


class TestSendHelp:
    def test_lfg_help_includes_usage_and_available_games(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, LFG_COMMAND))

        # The usage instructions stay in the content; the games list (which
        # grows with the number of games) goes in an embed.
        content, embeds = interaction.response.messages[0][0], interaction.response.messages[0][1]
        assert f"# Help: /{LFG_COMMAND}" in content
        assert f"`/{LFG_COMMAND} game:<game>" in content
        games_embed = embeds[0]
        assert games_embed.title == "Available games"
        assert "- `game_a`" in games_embed.description
        assert "<@&111>" in games_embed.description
        assert "No games are configured" not in games_embed.description

    def test_rename_help_includes_usage(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, RENAME_COMMAND))

        content = interaction.response.messages[0][0]
        assert interaction.response.messages[0][1] is None
        assert f"# Help: /{RENAME_COMMAND}" in content
        assert f"`/{RENAME_COMMAND} title:<new title>`" in content
        assert f"`/{RENAME_COMMAND}` without arguments" in content

    def test_games_help_includes_subcommands_and_option_rules(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, GAMES_COMMAND))

        # Plain content only: the text fits well within Discord's message
        # limit, so no embed is needed.
        content = interaction.response.messages[0][0]
        assert interaction.response.messages[0][1] is None
        assert f"# Help: /{GAMES_COMMAND}" in content
        assert f"`/{GAMES_COMMAND} add command:<command> [options...]`" in content
        assert f"`/{GAMES_COMMAND} update command:<command> [options...]`" in content
        assert f"`/{GAMES_COMMAND} remove command:<command>`" in content
        assert f"`/{GAMES_COMMAND} list`" in content
        # The option rules and guard conditions are documented.
        assert "role mention" in content
        assert "forum channel mention" in content
        assert "1-32 lowercase letters" in content
        assert "server managers" in content
        assert "database mode" in content
        assert len(content) <= constants.MESSAGE_CONTENT_LIMIT

    def test_game_command_help_signals_alias_and_pastes_lfg_help(self, matchmaking):
        # game_b has no configured parameters, so its help is exactly the /lfg
        # usage plus a games embed (parametrized games additionally get a
        # parameters embed, covered in TestGameParametersHelp).
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, "game_b"))

        content, embeds = interaction.response.messages[0][0], interaction.response.messages[0][1]
        assert "# Help: /game_b" in content
        assert f"`/game_b` is a shortcut for `/{LFG_COMMAND} game:game_b`" in content
        # One embed: the games list.
        assert [embed.title for embed in embeds] == ["Available games"]

        # Everything after the alias note is the exact /lfg usage.
        lfg_interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(lfg_interaction, LFG_COMMAND))
        lfg_content = lfg_interaction.response.messages[0][0]
        assert content.endswith(lfg_content.split("\n", 1)[1])
        # The games list is in the embed (the title carries the heading).
        assert "- `game_a`" in embeds[0].description

    def test_unknown_topic_falls_back_to_generic_help(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, "unknown"))
        content = interaction.response.messages[0][0]
        assert interaction.response.messages[0][1] is None
        assert "# Help: /unknown" in content
        assert "No detailed help is available" in content


class TestMinimalDynamicGame:
    """A game added via /games add with only a command has every option unset
    (None); the LFG flow must treat those as empty instead of crashing."""

    def _minimal_game_option(self):
        return GameOption(
            name=None, command="minimal", role=None, icon=None, color=None,
            forum=None, tag=None, visibility=None, message=None,
            registration_api=None, match_api=None, match_url=None,
            api_token_env_var=None, website_url=None, registration_url=None,
            profile_url=None, default_max_guests=None)

    def test_none_fields_are_normalized_to_empty_strings(self):
        game = self._minimal_game_option()
        assert game.name == ""
        assert game.role == ""
        assert game.icon == ""
        assert game.color == ""
        assert game.forum == ""
        assert game.tag == ""
        assert game.visibility == ""
        assert game.message == ""
        assert game.registration_api == ""
        assert game.match_api == ""
        assert game.match_url == ""
        assert game.api_token_env_var == ""
        assert game.website_url == ""
        assert game.registration_url == ""
        assert game.profile_url == ""
        assert game.default_max_guests is None
        assert game.command == "minimal"
        assert game.settings_summary() == []

    @pytest.mark.asyncio
    async def test_create_lfg_does_not_crash_on_unset_fields(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"))
        await matchmaking.create_lfg(
            interaction, self._minimal_game_option(), "desc", None)
        content, _, embed, _ = interaction.followup.sent[0]
        assert content == ""  # no role to ping
        assert "Looking for" in embed.title
        assert all(field.name != "Target" for field in embed.fields)
        # No icon configured -> falls back to the default avatar.
        assert embed.thumbnail is not None

    @pytest.mark.asyncio
    async def test_create_game_thread_uses_forum_mention_and_tolerates_none(
            self, matchmaking):
        # A configured forum: the thread goes there (the fake's LFG-channel
        # branch cannot build a real thread object). message=None is treated
        # as empty and must not crash the game-start ping.
        forum_channel = FakeChannel(
            id=555, name="root-forum", type_=discord.ChannelType.forum)
        matchmaking.bot._channels = {555: forum_channel}
        game_option = self._minimal_game_option()
        game_option.forum = "<#555>"
        message = FakeMessage([discord.Embed(description="desc")])
        interaction = FakeInteraction(user=FakeMember(1, "host"), message=message)
        context = LFGContext(game_option=game_option, host=interaction.user)
        await matchmaking.create_game_thread(interaction, context)
        assert forum_channel.created_kwargs["name"] == "desc"


class TestHelpLengths:
    """Help content and embed descriptions stay within Discord's limits."""

    def _matchmaking_with_many_games(self):
        # 200 games with roles: the games list would overflow the embed
        # description without the truncation guard.
        config = configparser.ConfigParser()
        commands = ", ".join(f"game{i}" for i in range(200))
        names = ", ".join(f"Game {i}" for i in range(200))
        roles = ", ".join(f"<@&{100 + i}>" for i in range(200))
        config.read_string(
            "[DEFAULT]\n"
            f"GamesCommands = {commands}\n"
            f"GamesFullNames = {names}\n"
            f"GamesRoles = {roles}\n"
        )
        params = configparser.ConfigParser()
        params.read_string(
            "[game0]\n"
            "setup = game_setup: (a, Alpha), (b, Beta)\n"
            "landmarks = landmarks: (x, X), (y, Y), (z, Z)\n"
        )
        return Matchmaking(bot=FakeBot(), config=config, game_parameters=params)

    def test_long_help_stays_within_discord_limits(self):
        matchmaking = self._matchmaking_with_many_games()

        for topic in (LFG_COMMAND, "game0", RENAME_COMMAND):
            interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
            _run(matchmaking.send_help(interaction, topic))

            content, embeds = (interaction.response.messages[0][0],
                               interaction.response.messages[0][1])
            assert len(content) <= constants.MESSAGE_CONTENT_LIMIT
            for embed in (embeds or []):
                assert len(embed.description) <= constants.EMBED_DESCRIPTION_LIMIT

    def test_games_embed_is_truncated_when_too_long(self):
        matchmaking = self._matchmaking_with_many_games()
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        _run(matchmaking.send_help(interaction, LFG_COMMAND))

        embeds = interaction.response.messages[0][1]
        games_embed = embeds[0]
        # Truncated to exactly the limit, signalling the cut.
        assert len(games_embed.description) == constants.EMBED_DESCRIPTION_LIMIT
        assert games_embed.description.endswith("...")


class TestRenameCommand:
    """Tests for the standalone /rename command."""

    def _thread_interaction(self, matchmaking, user, host, owner_id=None,
                            channel_type=None):
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)

        channel = FakeChannel(id=123, name="game-a")
        channel.type = channel_type or discord.ChannelType.public_thread
        channel.owner_id = owner_id if owner_id is not None else matchmaking.bot.user.id
        channel.message = FakeMessage([embed])

        return FakeInteraction(user=user, channel=channel)

    def test_requires_a_thread(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(user=host)
        _run(Matchmaking.rename.callback(matchmaking, interaction, title="New Room"))
        assert (
            interaction.response.messages[0][0]
            == "This command can only be used inside a bot-created game thread."
        )

    def test_rejects_thread_not_owned_by_bot(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = self._thread_interaction(
            matchmaking, user=host, host=host, owner_id=999
        )
        _run(Matchmaking.rename.callback(matchmaking, interaction, title="New Room"))
        assert (
            interaction.response.messages[0][0]
            == "This thread cannot be renamed because it was not created by this bot."
        )

    def test_non_host_cannot_rename(self, matchmaking):
        host = FakeMember(100, "Hosty")
        other = FakeMember(101, "Rando")
        interaction = self._thread_interaction(matchmaking, user=other, host=host)
        _run(Matchmaking.rename.callback(matchmaking, interaction, title="New Room"))
        assert interaction.channel.edited_kwargs is None
        assert (
            interaction.response.messages[0][0]
            == "Only the host can rename this thread."
        )

    @pytest.mark.asyncio
    async def test_host_renames_thread_directly(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = self._thread_interaction(matchmaking, user=host, host=host)

        await Matchmaking.rename.callback(matchmaking, interaction, title="New Room")

        assert interaction.channel.edited_kwargs["name"] == "New Room"
        assert interaction.response.messages[0][0] == "Thread renamed to **New Room**."

    @pytest.mark.asyncio
    async def test_empty_title_opens_modal(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = self._thread_interaction(matchmaking, user=host, host=host)

        await Matchmaking.rename.callback(matchmaking, interaction, title="   ")

        assert len(interaction.response.modals) == 1
        assert isinstance(interaction.response.modals[0], ThreadRenameModal)
        assert interaction.channel.edited_kwargs is None

    @pytest.mark.asyncio
    async def test_modal_callback_renames_thread(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = self._thread_interaction(matchmaking, user=host, host=host)

        await matchmaking.rename_thread_modal(interaction, "New Room")

        assert interaction.channel.edited_kwargs["name"] == "New Room"
        assert interaction.response.messages[0][0] == "Thread renamed to **New Room**."

    @pytest.mark.asyncio
    async def test_modal_callback_rejects_non_host(self, matchmaking):
        host = FakeMember(100, "Hosty")
        other = FakeMember(101, "Rando")
        interaction = self._thread_interaction(matchmaking, user=other, host=host)

        await matchmaking.rename_thread_modal(interaction, "New Room")

        assert interaction.channel.edited_kwargs is None
        assert (
            interaction.response.messages[0][0]
            == "Only the host can rename this thread."
        )


class TestLfgLocationRestrictions:
    def test_disallows_inside_thread(self, matchmaking):
        host = FakeMember(100, "Hosty")
        channel = FakeChannel(id=123, name="game-a")
        channel.type = discord.ChannelType.public_thread
        interaction = FakeInteraction(user=host, channel=channel)

        _run(Matchmaking.lfg.callback(matchmaking, interaction, game="game_a"))

        assert (
            interaction.response.messages[0][0]
            == f"The `/{LFG_COMMAND}` command cannot be used inside a thread. "
            f"Use `/{RENAME_COMMAND}` to rename a game thread."
        )
        assert interaction.channel.created_kwargs is None

    def test_disallows_outside_a_guild_channel(self, matchmaking):
        host = FakeMember(100, "Hosty")
        # A DM channel (private) is not a server channel.
        channel = FakeChannel(id=123, name="dm")
        channel.type = discord.ChannelType.private
        interaction = FakeInteraction(user=host, channel=channel)

        _run(Matchmaking.lfg.callback(matchmaking, interaction, game="game_a"))

        assert (
            interaction.response.messages[0][0]
            == f"The `/{LFG_COMMAND}` command can only be used in a server channel."
        )
        assert interaction.channel.created_kwargs is None


class TestLfgContextFromInteraction:
    def _build_fixture(self):
        host = FakeMember(100, "Hosty")
        guest1 = FakeMember(101, "G1")
        guest2 = FakeMember(102, "G2")
        role = FakeMentionable(777, "lfg")

        guild = FakeGuild(
            id=1,
            members={m.id: m for m in (host, guest1, guest2)},
            roles={777: role},
        )

        embed = discord.Embed(title="Looking for a Game A game", description="Desc")
        embed.add_field(name="Target", value="<@&777>", inline=True)
        embed.add_field(name="Host", value=host.mention, inline=True)
        embed.add_field(
            name="Guests (2/4)", value=f"{guest1.mention}, {guest2.mention}",
            inline=False,
        )
        embed.add_field(name="Subscribed", value=host.mention, inline=False)

        interaction = FakeInteraction(
            user=guest1, guild=guild, message=FakeMessage([embed])
        )
        return interaction, host, guest1, guest2, role

    @pytest.mark.asyncio
    async def test_reconstructs_full_context(self, matchmaking):
        interaction, host, guest1, guest2, role = self._build_fixture()
        context = await LFGContext.from_interaction(matchmaking, interaction)

        assert context.game_option == matchmaking.default_guild_config.games["game_a"]
        assert context.host is host
        assert context.target_role is role
        assert context.max_guests == 4
        assert context.guests == {guest1, guest2}
        assert context.users_to_notify == {host}

    @pytest.mark.asyncio
    async def test_empty_message_returns_empty_context(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"))
        context = await LFGContext.from_interaction(matchmaking, interaction)
        assert context.host is None
        assert context.guests == set()
        assert context.users_to_notify == set()
        assert context.game_option is None


class TestGameAutocomplete:
    @pytest.mark.asyncio
    async def test_filters_default_games_by_current(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await matchmaking.game_autocomplete(interaction, "game_a")
        assert [choice.value for choice in choices] == ["game_a"]

    @pytest.mark.asyncio
    async def test_matches_partial_case_insensitive(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await matchmaking.game_autocomplete(interaction, "GAME_B")
        assert [choice.value for choice in choices] == ["game_b"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        assert await matchmaking.game_autocomplete(interaction, "unreal") == []

    @pytest.mark.asyncio
    async def test_uses_guild_specific_games(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=90401)
        choices = await matchmaking.game_autocomplete(interaction, "")
        assert [choice.value for choice in choices] == ["game_c"]


class TestProcessJoin:
    def _context(self, host, **kwargs):
        return LFGContext(host=host, max_guests=4, users_to_notify=set(), **kwargs)

    @pytest.mark.asyncio
    async def test_host_cannot_join_own_game(self, matchmaking):
        host = FakeMember(100, "Hosty")
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)
        interaction = FakeInteraction(
            user=host,
            guild=FakeGuild(id=1, members={100: host}),
            message=FakeMessage([embed]),
        )
        context = self._context(host)

        await matchmaking.process_join(interaction, context)

        content = interaction.response.messages[0][0]
        assert content == "You are the host of this game."
        assert context.guests == set()

    @pytest.mark.asyncio
    async def test_full_game_rejects_new_guest(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guest1 = FakeMember(102, "G2")
        guest2 = FakeMember(103, "G3")
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)
        interaction = FakeInteraction(
            user=guest1,
            guild=FakeGuild(id=1, members={m.id: m for m in (host, guest1, guest2)}),
            message=FakeMessage([embed]),
        )
        context = LFGContext(
            host=host, max_guests=1, guests={guest2}, users_to_notify=set()
        )

        await matchmaking.process_join(interaction, context)

        content = interaction.response.messages[0][0]
        assert content == "Sorry, this game is already full."
        assert guest1 not in context.guests

    @pytest.mark.asyncio
    async def test_guest_joins_and_embed_updates(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guest1 = FakeMember(101, "G1")
        guild = FakeGuild(id=1, members={m.id: m for m in (host, guest1)})

        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)
        embed.add_field(name="Guests (0/4)", value="", inline=False)

        message = FakeMessage([embed])
        interaction = FakeInteraction(
            user=guest1, guild=guild, message=message, channel=FakeChannel()
        )
        context = self._context(host)

        await matchmaking.process_join(interaction, context)

        assert context.guests == {guest1}
        assert message.edited is not None
        updated_embed = message.edited["embed"]
        field_names = [field.name for field in updated_embed.fields]
        assert "Guests (1/4)" in field_names
        followup = interaction.followup.sent[0][0]
        assert followup == "You have joined the game!"


class TestSettingsPersistence:
    @pytest.mark.asyncio
    async def test_settings_survive_join_rebuild(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guest = FakeMember(101, "Guesty")
        guild = FakeGuild(id=1, members={host.id: host, guest.id: guest})

        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Target", value="<@&111>", inline=True)
        embed.add_field(name="Host", value=host.mention, inline=True)
        embed.add_field(name="Guests (0/4)", value="", inline=False)
        embed.add_field(name="Settings", value="param1: Alpha One, Delta Four\nparam2: First Choice", inline=False)

        message = FakeMessage([embed])
        interaction = FakeInteraction(
            user=guest, guild=guild, message=message, channel=FakeChannel()
        )

        # Reconstruct the context exactly as a button press would, then join.
        # The Settings field shows display names; they normalize back to the
        # raw values stored in the context.
        context = await LFGContext.from_interaction(matchmaking, interaction)
        assert context.game_settings == {"param1": ["alpha", "delta"], "param2": ["first"]}

        await matchmaking.process_join(interaction, context)

        updated_embed = message.edited["embed"]
        field_names = [field.name for field in updated_embed.fields]
        assert "Settings" in field_names
        settings_field = next(
            field for field in updated_embed.fields if field.name == "Settings"
        )
        assert "param1: Alpha One, Delta Four" in settings_field.value
        assert "param2: First Choice" in settings_field.value


class TestProcessCancel:
    @pytest.mark.asyncio
    async def test_non_host_cannot_cancel(self, matchmaking):
        host = FakeMember(100, "Hosty")
        other = FakeMember(101, "Rando")
        interaction = FakeInteraction(user=other)
        context = LFGContext(host=host)

        await matchmaking.process_cancel(interaction, context)

        assert (
            interaction.followup.sent[0][0]
            == "Only the host can cancel the game."
        )

    @pytest.mark.asyncio
    async def test_host_cancel_edits_message(self, matchmaking):
        host = FakeMember(100, "Hosty")
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)
        message = FakeMessage([embed])
        interaction = FakeInteraction(
            user=host,
            guild=FakeGuild(id=1, members={100: host}),
            message=message,
        )
        context = LFGContext(host=host)

        await matchmaking.process_cancel(interaction, context)

        assert message.edited is not None
        assert interaction.followup.sent[0][0] == "The game has been canceled."
class TestGuildCommandRegistration:
    def test_registers_per_guild_commands_only(self, matchmaking):
        matchmaking.bot.provided_guild_ids = set()
        matchmaking.register_guild_commands()

        tree = matchmaking.bot.tree
        guild_cmds = tree.get_commands(guild=discord.Object(id=90401))
        assert [c.name for c in guild_cmds] == ["game_c"]

        # Default-section games (game_a, game_b) must not become guild commands.
        assert tree.get_commands() == []
        assert matchmaking.bot.provided_guild_ids == {90401}

    def test_generated_command_has_optional_params(self, matchmaking):
        command = matchmaking._make_game_command("game_a")
        expected = {"description", "max_players", *matchmaking.game_parameters["game_a"]}
        assert expected <= set(command._params.keys())
        assert all(not param.required for param in command._params.values())
        # Every game parameter needs an autocomplete wired up.
        for param_name in matchmaking.game_parameters["game_a"]:
            assert command._params[param_name].autocomplete is not None

    def test_generated_callback_signature(self, matchmaking):
        callback = matchmaking._make_game_callback("game_a")
        names = list(inspect.signature(callback).parameters)
        # The always-present arguments plus one argument per configured
        # parameter of the game (derived from the fixture config).
        assert set(names) == (
            {"interaction", "description", "max_players"}
            | set(matchmaking.game_parameters["game_a"])
        )

    def test_skips_invalid_command_names(self):
        # "c&c" (illegal characters) and "GAME_A" (upper-case) stay usable through
        # /lfg but cannot become slash commands; constructing their
        # app_commands.Command would raise ValueError and crash the extension.
        config = configparser.ConfigParser()
        config.read_string(
            "[DEFAULT]\n"
            "GamesCommands = game_b, c&c, GAME_A\n"
            "GamesFullNames = Game B, Game & Subtitle, Game A Upper\n"
            "GamesRoles = <@&111>, <@&222>, <@&333>\n"
            "\n"
            "[GuildA]\n"
            "ID = 90401\n"
        )
        bot = FakeBot()
        cog = Matchmaking(bot=bot, config=config)

        cog.register_guild_commands()  # must not raise

        guild_cmds = bot.tree.get_commands(guild=discord.Object(id=90401))
        assert [c.name for c in guild_cmds] == ["game_b"]
        # The guild still has a valid command, so it remains tracked for sync.
        assert bot.provided_guild_ids == {90401}


class TestParamAutocomplete:
    @pytest.mark.asyncio
    async def test_filters_single_token(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        choices = await autocomplete(interaction, "al")
        # The choice is the full composed string (name == value), so whichever
        # the client writes into the field, the prefix is preserved.
        assert [(c.name, c.value) for c in choices] == [("Alpha One", "Alpha One")]

    @pytest.mark.asyncio
    async def test_filters_by_display_name(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        # Filtering matches the display name too, not just the raw value.
        choices = await autocomplete(interaction, "gamma t")
        assert [(c.name, c.value) for c in choices] == [("Gamma Three", "Gamma Three")]

    @pytest.mark.asyncio
    async def test_composes_choices_with_existing_prefix(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        # Discord replaces the whole field with the picked choice's name, so
        # after a comma the choices carry the existing picks as a prefix and
        # picking one appends it instead of overwriting it. Name and value are
        # both the composed display-name string, so whichever the client writes
        # the prefix survives; _parse_param_values resolves display names back
        # to raw values. The already-present value is not suggested again.
        choices = await autocomplete(interaction, "beta,")

        composed = {"beta,Alpha One", "beta,Gamma Three", "beta,Delta Four"}
        assert {c.value for c in choices} == composed
        assert {c.name for c in choices} == composed

    @pytest.mark.asyncio
    async def test_prefix_with_trailing_space(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        choices = await autocomplete(interaction, "alpha, b")

        assert [(c.name, c.value) for c in choices] == [("alpha,Beta Two", "alpha,Beta Two")]

    @pytest.mark.asyncio
    async def test_composed_values_over_100_chars_are_skipped(self, matchmaking):
        long_value = "x" * 99
        matchmaking.game_parameters["game_a"] = {
            "param1": {long_value: long_value, "ok": "OK", "ok2": "OK2"}}
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        # Without a prefix the 99-char value still fits the 100-char cap.
        choices = await autocomplete(interaction, "")
        assert {c.value for c in choices} == {long_value, "OK", "OK2"}

        # With a prefix, composing the 99-char value would exceed the cap, so
        # only the short values are offered.
        choices = await autocomplete(interaction, "ok,")
        assert {c.value for c in choices} == {"ok,OK2"}


class TestParamParsing:
    ACCEPTED = {
        "alpha": "Alpha One",
        "beta": "Beta Two",
        "gamma": "Gamma Three",
        "delta": "Delta Four",
    }

    def test_valid_multi_values(self, matchmaking):
        values, invalid = matchmaking._parse_param_values(
            "alpha,beta", self.ACCEPTED)
        assert invalid is None
        assert values == ["alpha", "beta"]

    def test_invalid_values_reported(self, matchmaking):
        values, invalid = matchmaking._parse_param_values(
            "alpha,epsilon", self.ACCEPTED)
        assert values is None
        assert invalid == ["epsilon"]

    def test_none_is_skipped(self, matchmaking):
        assert matchmaking._parse_param_values(None, self.ACCEPTED) == (None, None)

    def test_empty_string_yields_no_values(self, matchmaking):
        values, invalid = matchmaking._parse_param_values("", self.ACCEPTED)
        assert invalid is None
        assert values == []

    def test_display_names_are_normalized_to_values(self, matchmaking):
        # A client may commit an autocomplete choice by writing its display
        # name; display names resolve back to raw values (case-insensitively).
        values, invalid = matchmaking._parse_param_values(
            "Alpha One, beta", self.ACCEPTED)
        assert invalid is None
        assert values == ["alpha", "beta"]

    def test_display_name_case_insensitive(self, matchmaking):
        values, invalid = matchmaking._parse_param_values(
            "alpha one", self.ACCEPTED)
        assert invalid is None
        assert values == ["alpha"]


class TestGameCommandModal:
    """Guided (modal) route of the per-game slash commands."""

    @staticmethod
    def _modal_stub(description="let's play", max_players_number=None):
        return SimpleNamespace(
            description=SimpleNamespace(value=description),
            max_players_number=max_players_number,
        )

    def test_no_arguments_opens_settings_modal(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(matchmaking._run_game_command(interaction, "game_a", {}))

        assert len(interaction.response.modals) == 1
        assert isinstance(interaction.response.modals[0], GameSettingsModal)
        # The modal is the response; nothing else was sent.
        assert interaction.response.messages == []

    def test_any_argument_skips_modal_and_goes_direct(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(matchmaking._run_game_command(
            interaction, "game_a", {"description": "hi"}))

        assert interaction.response.modals == []
        # Direct route: deferred and the LFG post was created.
        assert interaction.response.deferred is not None
        assert interaction.followup.sent

    def test_game_modal_confirm_creates_lfg(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        confirmation = FakeInteraction(user=host, guild=guild)

        _run(matchmaking._create_lfg_from_modal(
            confirmation, self._modal_stub(max_players_number=4), "game_a"))

        embed = confirmation.followup.sent[0][2]
        guests = [f.name for f in embed.fields if f.name.startswith("Guests")]
        # Modal max_players=4 -> 3 guests.
        assert guests == ["Guests (0/3)"]

    def test_game_modal_confirm_uses_default_max_guests(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        command = FakeInteraction(user=host, guild=guild)
        confirmation = FakeInteraction(user=host, guild=guild)

        _run(matchmaking._run_game_command(command, "game_a", {}))
        modal = command.response.modals[0]
        _run(modal.on_confirm(confirmation, self._modal_stub(), None))

        embed = confirmation.followup.sent[0][2]
        guests = [f.name for f in embed.fields if f.name.startswith("Guests")]
        # Fixture game_a default max players = 5 -> 4 guests.
        assert guests == ["Guests (0/4)"]

    def test_guided_lfg_selection_still_shares_modal_tail(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)
        select = SimpleNamespace(values=["game_a"])

        _run(matchmaking.process_game_settings(
            interaction, self._modal_stub(max_players_number=2), select))

        embed = interaction.followup.sent[0][2]
        guests = [f.name for f in embed.fields if f.name.startswith("Guests")]
        assert guests == ["Guests (0/1)"]


class FakeMatchApiResponse:
    """Stands in for aiohttp's response inside register_match."""

    def __init__(self, status, payload, error_text=""):
        self.status = status
        self._payload = payload
        self._error_text = error_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._error_text

    async def json(self):
        return self._payload


class FakeMatchApiSession:
    """Stands in for aiohttp.ClientSession inside register_match."""

    def __init__(self, metadata=None, options_status=200,
                 post_status=201, post_payload=None, error_text="",
                 post_exception=None):
        self.metadata = metadata
        self.options_status = options_status
        self.post_status = post_status
        self.post_payload = post_payload if post_payload is not None else {"id": 42}
        self.error_text = error_text
        self.post_exception = post_exception
        self.posted = None
        self.options_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def options(self, url):
        self.options_calls += 1
        return FakeMatchApiResponse(self.options_status, self.metadata)

    def post(self, url, json=None):
        self.posted = json
        if self.post_exception is not None:
            raise self.post_exception
        return FakeMatchApiResponse(self.post_status, self.post_payload, self.error_text)


class FakeThread:
    """Stands in for a discord.Thread inside register_match."""

    def __init__(self):
        self.sent = []
        self.jump_url = "https://discord.com/channels/1/1/1"

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class TestAddGameSettingsPayload:
    FIELD_MAP = {"param1": "field_one", "param2": "field_two"}
    MULTI_FIELDS = {"field_two"}

    def test_single_value_wired_when_exactly_one(self, matchmaking):
        payload = {}
        matchmaking._add_game_settings_payload(
            payload, {"param1": ["alpha"]}, self.FIELD_MAP, self.MULTI_FIELDS)
        assert payload == {"field_one": "alpha"}

    def test_single_value_left_blank_when_multiple(self, matchmaking):
        payload = {}
        matchmaking._add_game_settings_payload(
            payload, {"param1": ["alpha", "beta"]}, self.FIELD_MAP, self.MULTI_FIELDS)
        assert payload == {}

    def test_single_value_left_blank_when_empty(self, matchmaking):
        payload = {}
        matchmaking._add_game_settings_payload(
            payload, {"param1": []}, self.FIELD_MAP, self.MULTI_FIELDS)
        assert payload == {}

    def test_multi_value_wired_as_list(self, matchmaking):
        payload = {}
        matchmaking._add_game_settings_payload(
            payload, {"param2": ["first", "second"]}, self.FIELD_MAP, self.MULTI_FIELDS)
        assert payload == {"field_two": ["first", "second"]}

    def test_multi_value_with_single_value_still_list(self, matchmaking):
        payload = {}
        matchmaking._add_game_settings_payload(
            payload, {"param2": ["first"]}, self.FIELD_MAP, self.MULTI_FIELDS)
        assert payload == {"field_two": ["first"]}

    def test_parameter_without_field_is_ignored(self, matchmaking):
        payload = {"title": "T"}
        matchmaking._add_game_settings_payload(
            payload, {"param3": ["yes"]}, self.FIELD_MAP, self.MULTI_FIELDS)
        assert payload == {"title": "T"}

    def test_unknown_parameters_ignored(self, matchmaking):
        payload = {"title": "T"}
        matchmaking._add_game_settings_payload(
            payload, {"unknown": ["x"]}, self.FIELD_MAP, self.MULTI_FIELDS)
        assert payload == {"title": "T"}

    def test_unknown_metadata_treats_fields_as_single(self, matchmaking):
        # Metadata failure (None): every field is wired as single-valued.
        payload = {}
        matchmaking._add_game_settings_payload(
            payload, {"param1": ["alpha"]}, self.FIELD_MAP, None)
        assert payload == {"field_one": "alpha"}
        payload = {}
        matchmaking._add_game_settings_payload(
            payload, {"param1": ["alpha", "beta"]}, self.FIELD_MAP, None)
        assert payload == {}

    def test_empty_settings_leave_payload_unchanged(self, matchmaking):
        payload = {"title": "T"}
        matchmaking._add_game_settings_payload(payload, None, self.FIELD_MAP, set())
        matchmaking._add_game_settings_payload(payload, {}, self.FIELD_MAP, set())
        assert payload == {"title": "T"}


METADATA = {
    "actions": {
        "POST": {
            "field_one": {"type": "string"},
            "field_two": {"type": "multiple_choice"},
        }
    }
}


class TestGetMultiValueFields:
    def test_parses_multi_value_types(self, matchmaking, monkeypatch):
        session = FakeMatchApiSession(metadata=METADATA)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)

        fields = _run(matchmaking._get_multi_value_fields("https://api/match/"))

        assert fields == {"field_two"}
        assert session.options_calls == 1

    def test_is_cached_per_url(self, matchmaking, monkeypatch):
        session = FakeMatchApiSession(metadata=METADATA)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)

        first = _run(matchmaking._get_multi_value_fields("https://api/match/"))
        second = _run(matchmaking._get_multi_value_fields("https://api/match/"))

        assert first == second == {"field_two"}
        assert session.options_calls == 1

    def test_failed_metadata_returns_none(self, matchmaking, monkeypatch):
        session = FakeMatchApiSession(metadata=METADATA, options_status=500)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)

        fields = _run(matchmaking._get_multi_value_fields("https://api/match/"))

        assert fields is None
        # Failures are not cached, so the metadata is retried next time.
        assert matchmaking._match_api_metadata == {}

    def test_parses_drf_multiple_choice_label(self, matchmaking, monkeypatch):
        # DRF's SimpleMetadata labels MultipleChoiceField as "multiple choice"
        # (with a space) — the label landmarks and hirelings get on this API.
        metadata = {
            "actions": {
                "POST": {
                    "landmarks": {"type": "multiple choice"},
                    "board_map": {"type": "choice"},
                }
            }
        }
        session = FakeMatchApiSession(metadata=metadata)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)

        fields = _run(matchmaking._get_multi_value_fields("https://api/match/"))

        assert fields == {"landmarks"}


class TestGameApiFields:
    def test_reserved_and_param_fields_parsed(self, matchmaking):
        fields = matchmaking.game_api_fields["game_a"]
        # Reserved api_* keys become the fixed payload component field names.
        assert fields["api_title_field"] == "match_title"
        assert fields["api_table_talk_url_field"] == "discussion_url"
        assert fields["api_participants_field"] == "players"
        assert fields["api_discord_username_field"] == "discord_name"
        # Parameter field prefixes become param -> API field mappings.
        assert fields["param1"] == "field_one"
        assert fields["param2"] == "field_two"
        # param3 has no field prefix: it is a parameter but not wired.
        assert "param3" not in fields

    def test_reserved_keys_are_not_parameters(self, matchmaking):
        # Reserved api_* keys must not become slash command parameters.
        params = matchmaking.game_parameters["game_a"]
        assert set(params) == {"param1", "param2", "param3"}
        assert not any(key.startswith("api_") for key in params)

    def test_default_api_fields_are_inherited(self):
        # A game section without explicit api_* keys still gets the fixed
        # payload component field names from the [DEFAULT] section.
        params = configparser.ConfigParser()
        params.read_string(
            "[DEFAULT]\n"
            "api_title_field = title\n"
            "api_table_talk_url_field = table_talk_url\n"
            "api_participants_field = players\n"
            "api_discord_username_field = discord_name\n"
            "\n"
            "[bare_game]\n"
            "setup = game_setup: (a, Alpha)\n"
        )
        config = configparser.ConfigParser()
        config.read_string(
            "[DEFAULT]\n"
            "GamesCommands = bare_game\n"
            "GamesFullNames = Bare Game\n"
        )
        matchmaking = Matchmaking(bot=FakeBot(), config=config, game_parameters=params)

        fields = matchmaking.game_api_fields["bare_game"]
        assert fields["api_title_field"] == "title"
        assert fields["api_table_talk_url_field"] == "table_talk_url"
        assert fields["api_participants_field"] == "players"
        assert fields["api_discord_username_field"] == "discord_name"

    def test_default_api_fields_parsed_for_games_without_a_section(self):
        # The [DEFAULT] api_* keys are stored separately so games that have no
        # section in games_parameters.ini still get the fixed component names.
        params = configparser.ConfigParser()
        params.read_string(
            "[DEFAULT]\n"
            "api_title_field = title\n"
            "api_table_talk_url_field = table_talk_url\n"
            "api_participants_field = participants\n"
            "api_discord_username_field = discord_username\n"
        )
        config = configparser.ConfigParser()
        config.read_string(
            "[DEFAULT]\n"
            "GamesCommands = rootdig\n"
            "GamesFullNames = Root Digital\n"
        )
        matchmaking = Matchmaking(bot=FakeBot(), config=config, game_parameters=params)

        assert matchmaking.default_api_fields == {
            "api_title_field": "title",
            "api_table_talk_url_field": "table_talk_url",
            "api_participants_field": "participants",
            "api_discord_username_field": "discord_username",
        }
        # No section for rootdig, so game_api_fields has no entry for it...
        assert "rootdig" not in matchmaking.game_api_fields


class TestRegisterMatch:
    def test_success_posts_confirmation_and_wires_settings(self, matchmaking, monkeypatch):
        session = FakeMatchApiSession(metadata=METADATA, post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", "https://site/match/",
            None, "Title", "Game", [],
            game_settings={"param1": ["alpha"], "param2": ["first", "second"]},
            game_command="game_a"))

        # The payload component names come from the reserved api_* keys in the
        # fixture config, not from hardcoded names.
        assert session.posted["match_title"] == "Title"
        assert session.posted["discussion_url"] == thread.jump_url
        assert session.posted["field_one"] == "alpha"
        assert session.posted["field_two"] == ["first", "second"]
        assert thread.sent == ["Game preregistered on Game: https://site/match/42/"]

    def test_participants_wired_under_configured_names(self, matchmaking, monkeypatch):
        session = FakeMatchApiSession(metadata=METADATA, post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()
        users = [SimpleNamespace(name="player1"), SimpleNamespace(name="player2")]

        _run(matchmaking.register_match(
            thread, "https://api/match/", None,
            None, "Title", "Game", users,
            game_settings={}, game_command="game_a"))

        assert session.posted["players"] == [
            {"discord_name": "player1"}, {"discord_name": "player2"},
        ]

    def test_missing_api_fields_omit_components(self, matchmaking, monkeypatch):
        # game_b has no game_parameters section: no reserved api_* keys, so
        # title, thread link and participants are not sent.
        session = FakeMatchApiSession(metadata=METADATA, post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", None,
            None, "Title", "Game", [SimpleNamespace(name="p1")],
            game_settings={}, game_command="game_b"))

        assert session.posted == {}

    def test_api_reject_posts_failure_message_not_url(self, matchmaking, monkeypatch):
        session = FakeMatchApiSession(
            metadata=METADATA, post_status=400,
            error_text='{"field_two": ["max"]}')
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", "https://site/match/",
            None, "Title", "Game", [],
            game_settings={"param2": ["first", "second"]},
            game_command="game_a"))

        assert len(thread.sent) == 1
        assert "failed" in thread.sent[0].lower()
        assert "site/match" not in thread.sent[0]

    def test_api_unreachable_posts_failure_message(self, matchmaking, monkeypatch):
        # When the API cannot be reached at all (connection refused, DNS
        # failure, timeout, ...), the request raises an exception. A failure
        # message must be posted to the thread instead of a match URL, so the
        # players know to submit a new game entry manually.
        session = FakeMatchApiSession(
            metadata=METADATA,
            post_exception=aiohttp.ClientConnectionError("connection refused"))
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", "https://site/match/",
            None, "Title", "Game", [],
            game_settings={}, game_command="game_a"))

        assert len(thread.sent) == 1
        assert "failed" in thread.sent[0].lower()
        assert "site/match" not in thread.sent[0]

    def test_failure_message_includes_website_link(self, matchmaking, monkeypatch):
        # The failure message must direct players to the league website as a
        # hyperlink, so they can submit a new game entry manually.
        session = FakeMatchApiSession(
            metadata=METADATA, post_status=400,
            error_text='{"field_two": ["max"]}')
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", "https://site/match/",
            None, "Title", "League", [],
            game_settings={"param2": ["first", "second"]},
            game_command="game_a",
            website_url="https://www.league.example/"))

        assert len(thread.sent) == 1
        assert "[League](https://www.league.example/)" in thread.sent[0]

    def test_failure_message_website_name_only_without_url(self, matchmaking, monkeypatch):
        # When only the website name is known (no URL), it is still mentioned.
        session = FakeMatchApiSession(
            metadata=METADATA, post_status=400,
            error_text='{"field_two": ["max"]}')
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", "https://site/match/",
            None, "Title", "League", [],
            game_settings={"param2": ["first", "second"]},
            game_command="game_a"))

        assert len(thread.sent) == 1
        assert "League" in thread.sent[0]
        assert "[" not in thread.sent[0]

    def test_single_value_with_multiple_values_is_not_sent(self, matchmaking, monkeypatch):
        session = FakeMatchApiSession(metadata=METADATA, post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", None,
            None, "Title", "Game", [],
            game_settings={"param1": ["alpha", "beta"]},
            game_command="game_a"))

        assert "field_one" not in session.posted

    def test_missing_metadata_skips_multi_value_wiring(self, matchmaking, monkeypatch):
        # When the metadata cannot be read, fields are treated as single-valued
        # so multi-value parameters are not sent as lists.
        session = FakeMatchApiSession(metadata=METADATA, options_status=500,
                                      post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", None,
            None, "Title", "Game", [],
            game_settings={"param2": ["first", "second"]},
            game_command="game_a"))

        assert "field_two" not in session.posted

    def test_fixed_components_sent_via_default_inherited_fields(self, monkeypatch):
        # A game whose section declares no api_* keys still sends the title,
        # thread link and participants, thanks to the [DEFAULT] section.
        params = configparser.ConfigParser()
        params.read_string(
            "[DEFAULT]\n"
            "api_title_field = title\n"
            "api_table_talk_url_field = table_talk_url\n"
            "api_participants_field = participants\n"
            "api_discord_username_field = discord_username\n"
            "\n"
            "[bare_game]\n"
            "setup = game_setup: (a, Alpha)\n"
        )
        config = configparser.ConfigParser()
        config.read_string(
            "[DEFAULT]\n"
            "GamesCommands = bare_game\n"
            "GamesFullNames = Bare Game\n"
        )
        matchmaking = Matchmaking(bot=FakeBot(), config=config, game_parameters=params)

        session = FakeMatchApiSession(metadata=METADATA, post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()
        users = [SimpleNamespace(name="p1")]

        _run(matchmaking.register_match(
            thread, "https://api/match/", None,
            None, "Title", "Bare", users,
            game_settings={"setup": ["a"]}, game_command="bare_game"))

        assert session.posted["title"] == "Title"
        assert session.posted["table_talk_url"] == thread.jump_url
        assert session.posted["participants"] == [{"discord_username": "p1"}]
        assert session.posted["game_setup"] == "a"

    def test_multi_value_wired_with_drf_metadata(self, matchmaking, monkeypatch):
        # The real DRF metadata labels multiple-choice fields "multiple choice"
        # (with a space); they must be wired as lists in the payload.
        metadata = {
            "actions": {
                "POST": {
                    "field_one": {"type": "string"},
                    "field_two": {"type": "multiple choice"},
                }
            }
        }
        session = FakeMatchApiSession(metadata=metadata, post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()

        _run(matchmaking.register_match(
            thread, "https://api/match/", None,
            None, "Title", "Game", [],
            game_settings={"param1": ["alpha"], "param2": ["first", "second"]},
            game_command="game_a"))

        assert session.posted["field_one"] == "alpha"
        assert session.posted["field_two"] == ["first", "second"]


    def test_fixed_components_sent_for_game_without_parameters_section(self, monkeypatch):
        # A game with a match API but no section in games_parameters.ini still
        # sends title, thread link and participants via the [DEFAULT] api_* keys.
        params = configparser.ConfigParser()
        params.read_string(
            "[DEFAULT]\n"
            "api_title_field = title\n"
            "api_table_talk_url_field = table_talk_url\n"
            "api_participants_field = participants\n"
            "api_discord_username_field = discord_username\n"
        )
        config = configparser.ConfigParser()
        config.read_string(
            "[DEFAULT]\n"
            "GamesCommands = rootdig\n"
            "GamesFullNames = Root Digital\n"
        )
        matchmaking = Matchmaking(bot=FakeBot(), config=config, game_parameters=params)

        session = FakeMatchApiSession(metadata=METADATA, post_status=201, post_payload={"id": 42})
        monkeypatch.setattr(aiohttp, "ClientSession", lambda headers=None: session)
        thread = FakeThread()
        users = [SimpleNamespace(name="p1")]

        _run(matchmaking.register_match(
            thread, "https://api/match/", None,
            None, "Title", "Root Digital", users,
            game_command="rootdig"))

        assert session.posted["title"] == "Title"
        assert session.posted["table_talk_url"] == thread.jump_url
        assert session.posted["participants"] == [{"discord_username": "p1"}]


class TestLfgGameOnlyModal:
    """Modal route of /lfg when only the game argument is given."""

    @staticmethod
    def _modal_stub(description="let's play", max_players_number=None):
        return SimpleNamespace(
            description=SimpleNamespace(value=description),
            max_players_number=max_players_number,
        )

    def test_game_only_opens_settings_modal(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(matchmaking, interaction, game="game_a"))

        assert len(interaction.response.modals) == 1
        assert isinstance(interaction.response.modals[0], GameSettingsModal)
        # The modal is the response; nothing else was sent.
        assert interaction.response.messages == []

    def test_game_with_settings_goes_direct(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(
            matchmaking, interaction, game="game_a", description="hi"))

        assert interaction.response.modals == []
        # Direct route: deferred and the LFG post was created.
        assert interaction.response.deferred is not None
        assert interaction.followup.sent

    def test_game_only_unknown_game_rejected(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(matchmaking, interaction, game="unknown"))

        assert interaction.response.modals == []
        assert "not a configured game" in interaction.response.messages[0][0]

    def test_game_only_modal_confirm_creates_lfg(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        command = FakeInteraction(user=host, guild=guild)
        confirmation = FakeInteraction(user=host, guild=guild)

        _run(Matchmaking.lfg.callback(matchmaking, command, game="game_a"))
        modal = command.response.modals[0]
        _run(modal.on_confirm(confirmation, self._modal_stub(), None))

        embed = confirmation.followup.sent[0][2]
        guests = [f.name for f in embed.fields if f.name.startswith("Guests")]
        # Fixture game_a default max players = 5 -> 4 guests.
        assert guests == ["Guests (0/4)"]

    def test_direct_settings_without_game_still_rejected(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(matchmaking, interaction, max_players=4))

        assert "The `game` argument is required" in interaction.response.messages[0][0]


class TestGameParametersHelp:
    """Per-game help documents the game's configured parameters."""

    def _matchmaking_with_games(self, game_parameters_config):
        config = configparser.ConfigParser()
        config.read_string(
            "[DEFAULT]\n"
            "GamesCommands = game_a, game_b\n"
            "GamesFullNames = Game A, Game B\n"
            "GamesRoles = <@&111>, <@&222>\n"
            "\n"
        )
        return Matchmaking(bot=FakeBot(), config=config,
                           game_parameters=game_parameters_config)

    def test_parametrized_game_help_lists_parameters(self, game_parameters_config):
        matchmaking = self._matchmaking_with_games(game_parameters_config)
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        _run(matchmaking.send_help(interaction, "game_a"))

        # The alias note + /lfg usage stay in the content; the games list and
        # the parameters list are shown in embeds (they can be long).
        content, embeds = interaction.response.messages[0][0], interaction.response.messages[0][1]
        assert "# Help: /game_a" in content
        assert "Available games" not in content
        assert [embed.title for embed in embeds] == ["Available games", "Game parameters"]
        params_embed = embeds[1]
        description = params_embed.description
        assert "`/game_a` also accepts these arguments" in description
        assert "only available as command arguments" in description
        # Every configured parameter is listed with its display names only
        # (derived from the fixture config).
        for param_name, values in matchmaking.game_parameters["game_a"].items():
            assert f"- `{param_name}`: {', '.join(values.values())}" in description

    def test_game_without_parameters_has_no_section(self, game_parameters_config):
        matchmaking = self._matchmaking_with_games(game_parameters_config)
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        _run(matchmaking.send_help(interaction, "game_b"))

        content, embeds = interaction.response.messages[0][0], interaction.response.messages[0][1]
        # Only the games embed; no parameters embed.
        assert [embed.title for embed in embeds] == ["Available games"]
        # Still the alias help.
        assert "# Help: /game_b" in content


class TestCreateLfgSettings:
    @pytest.mark.asyncio
    async def test_renders_settings_field(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        interaction = FakeInteraction(user=host, guild=guild)
        game_option = matchmaking.default_guild_config.games["game_a"]

        await matchmaking.create_lfg(
            interaction, game_option, "desc", None,
            game_settings={"param1": ["alpha", "delta"], "param2": ["first"]},
        )

        embed = interaction.followup.sent[0][2]
        field_names = [field.name for field in embed.fields]
        assert "Settings" in field_names
        settings_value = [
            field.value for field in embed.fields if field.name == "Settings"
        ][0]
        # Raw values in the settings dict are rendered with their display names.
        assert "param1: Alpha One, Delta Four" in settings_value
        assert "param2: First Choice" in settings_value