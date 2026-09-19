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
from cogs.matchmaking.constants import (
    DEFAULT_GUILD_ID, DEFAULT_NB_GAMES, EMOJI_START, GAMES_COMMAND,
    LFG_COMMAND, LFG_FIELD_GAMES, MAX_NB_GAMES, RENAME_COMMAND,
)
from cogs.matchmaking.models import GameOption, LFGContext
from cogs.matchmaking.utils import has_lfg_view
from cogs.matchmaking.views import GameSettingsModal, ThreadRenameModal

from tests.conftest import (
    FakeBot,
    FakeChannel,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakeMessage,
    FakeMentionable,
    FakeThread,
    lfg_view_components,
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
        assert game_a.channel == "<#777>"  # GamesChannels index 0
        assert game_a.message == ""  # empty per config; index 1 holds the message

        game_b = games["game_b"]
        assert game_b.name == "Game B"
        assert game_b.icon == "https://example.com/icon.png"
        # Game B inherits GamesMaxPlayers=2 from DEFAULT -> default max guests = 1.
        assert game_b.default_max_guests == 1
        assert game_b.channel == ""  # blank GamesChannels entry
        assert game_b.message == "Please check the rules."

    def test_guild_specific_section_overrides_default(self, matchmaking):
        guild_config = matchmaking.guilds[90401]
        assert guild_config.guild_id == 90401
        game_c = guild_config.games["game_c"]
        assert game_c.name == "Game C"
        assert game_c.role == "<@&333>"
        assert game_c.default_max_guests == 3
        assert game_c.channel == "<#888>"

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
            forum=None, channel=None, tag=None, visibility=None, message=None,
            registration_api=None, match_api=None, match_url=None,
            api_token=None, website_url=None, registration_url=None,
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
        assert game.api_token == ""
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
            interaction, self._minimal_game_option(), None, "desc", None)
        # The LFG post goes out as a regular channel message (not an
        # interaction followup), and the deferred placeholder is removed.
        content, embed, _ = interaction.channel.sent[0]
        assert content == ""  # no role to ping
        assert "Looking for" in embed.title
        # The deferred placeholder is answered with an ephemeral confirmation.
        confirmation_content, confirmation_ephemeral, _, _ = interaction.followup.sent[0]
        assert confirmation_content == "The LFG post was created: https://discord.com/channels/1/1/1."
        assert confirmation_ephemeral is True
        assert all(field.name != "Target" for field in embed.fields)
        # No icon configured -> falls back to the default avatar.
        assert embed.thumbnail is not None

    @pytest.mark.asyncio
    async def test_create_lfg_failure_confirms_ephemerally(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"))

        async def failing_send(**kwargs):
            raise RuntimeError("cannot send")

        interaction.channel.send = failing_send
        await matchmaking.create_lfg(
            interaction, self._minimal_game_option(), None, "desc", None)

        assert interaction.channel.sent == []
        content, ephemeral, _, _ = interaction.followup.sent[0]
        assert content == (
            "The LFG post could not be created. "
            "Check that the bot has permission to send messages in this channel.")
        assert ephemeral is True

    @pytest.mark.asyncio
    async def test_start_game_matches_uses_forum_mention_and_tolerates_none(
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
        await matchmaking.start_game_matches(interaction, context)
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
        # Direct mode defers first: the starter-message fetch is a REST call.
        assert interaction.response.deferred is True
        assert (
            interaction.followup.sent[0][0]
            == "This command can only be used inside a bot-created game thread."
        )

    def test_rejects_thread_not_owned_by_bot(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = self._thread_interaction(
            matchmaking, user=host, host=host, owner_id=999
        )
        _run(Matchmaking.rename.callback(matchmaking, interaction, title="New Room"))
        assert interaction.response.deferred is True
        assert (
            interaction.followup.sent[0][0]
            == "This thread cannot be renamed because it was not created by this bot."
        )

    def test_non_host_cannot_rename(self, matchmaking):
        host = FakeMember(100, "Hosty")
        other = FakeMember(101, "Rando")
        interaction = self._thread_interaction(matchmaking, user=other, host=host)
        _run(Matchmaking.rename.callback(matchmaking, interaction, title="New Room"))
        assert interaction.response.deferred is True
        assert interaction.channel.edited_kwargs is None
        assert (
            interaction.followup.sent[0][0]
            == "Only the host can rename this thread."
        )

    @pytest.mark.asyncio
    async def test_host_renames_thread_directly(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = self._thread_interaction(matchmaking, user=host, host=host)

        await Matchmaking.rename.callback(matchmaking, interaction, title="New Room")

        assert interaction.response.deferred is True
        assert interaction.channel.edited_kwargs["name"] == "New Room"
        assert interaction.followup.sent[0][0] == "Thread renamed to **New Room**."

    @pytest.mark.asyncio
    async def test_host_renames_using_cached_starter_message(self, matchmaking):
        host = FakeMember(100, "Hosty")
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)

        channel = FakeChannel(id=123, name="game-a")
        channel.type = discord.ChannelType.public_thread
        channel.owner_id = matchmaking.bot.user.id
        # Only the cache holds the LFG embed: fetching by ID and the thread
        # history are both empty, so success proves the cached starter was
        # looked at first.
        channel.starter_message = FakeMessage([embed])
        channel.message = None
        channel.messages = []

        interaction = FakeInteraction(user=host, channel=channel)

        await Matchmaking.rename.callback(matchmaking, interaction, title="New Room")

        assert interaction.channel.edited_kwargs["name"] == "New Room"
        assert interaction.followup.sent[0][0] == "Thread renamed to **New Room**."

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

        assert interaction.response.deferred is True
        assert interaction.channel.edited_kwargs["name"] == "New Room"
        assert interaction.followup.sent[0][0] == "Thread renamed to **New Room**."

    @pytest.mark.asyncio
    async def test_modal_callback_rejects_non_host(self, matchmaking):
        host = FakeMember(100, "Hosty")
        other = FakeMember(101, "Rando")
        interaction = self._thread_interaction(matchmaking, user=other, host=host)

        await matchmaking.rename_thread_modal(interaction, "New Room")

        assert interaction.response.deferred is True
        assert interaction.channel.edited_kwargs is None
        assert (
            interaction.followup.sent[0][0]
            == "Only the host can rename this thread."
        )

    def _non_forum_thread_interaction(self, matchmaking, user, host,
                                      channel_type):
        """A bot-created thread whose LFG embed is not in the thread itself.

        A thread created under a message shares that message's ID, but the
        message stays in the parent channel: fetching it through the thread
        fails, and it must be fetched through ``thread.parent``. A private
        thread created from a channel has no parent message at all — the bot
        posts the LFG embed into the thread instead, so it is only reachable
        through the thread history.
        """
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)

        parent = FakeChannel(id=999, name="lfg-channel")
        parent.type = discord.ChannelType.text

        channel = FakeChannel(id=123, name="game-a")
        channel.type = channel_type
        channel.owner_id = matchmaking.bot.user.id
        channel.parent = parent
        channel.message = None  # fetch_message on the thread itself: nothing
        if (channel_type == discord.ChannelType.private_thread):
            # No parent message: the LFG embed was posted into the thread.
            parent.message = None
            channel.messages = [FakeMessage([embed])]
        else:
            # The LFG message lives in the parent channel, keyed by thread.id.
            parent.message = FakeMessage([embed])

        return FakeInteraction(user=user, channel=channel)

    @pytest.mark.asyncio
    async def test_host_renames_thread_created_outside_forum(self, matchmaking):
        host = FakeMember(100, "Hosty")
        for channel_type in (discord.ChannelType.public_thread,
                             discord.ChannelType.private_thread):
            interaction = self._non_forum_thread_interaction(
                matchmaking, user=host, host=host, channel_type=channel_type)

            await Matchmaking.rename.callback(
                matchmaking, interaction, title="New Room")

            assert interaction.response.deferred is True
            assert interaction.channel.edited_kwargs["name"] == "New Room"
            assert (
                interaction.followup.sent[0][0]
                == "Thread renamed to **New Room**."
            )

    @pytest.mark.asyncio
    async def test_non_host_cannot_rename_thread_created_outside_forum(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        other = FakeMember(101, "Rando")
        interaction = self._non_forum_thread_interaction(
            matchmaking, user=other, host=host,
            channel_type=discord.ChannelType.public_thread)

        await Matchmaking.rename.callback(
            matchmaking, interaction, title="New Room")

        assert interaction.response.deferred is True
        assert interaction.channel.edited_kwargs is None
        assert (
            interaction.followup.sent[0][0]
            == "Only the host can rename this thread."
        )

    def _host_not_found_interaction(self, matchmaking, user, channel_type=None):
        """A bot-created thread whose host can no longer be identified.

        The LFG message is unreachable (fetch_message returns nothing and the
        thread history holds no embed with a Host field), e.g. because the
        LFG message was deleted.
        """
        channel = FakeChannel(id=123, name="game-a")
        channel.type = channel_type or discord.ChannelType.public_thread
        channel.owner_id = matchmaking.bot.user.id
        channel.message = None
        channel.messages = []

        return FakeInteraction(user=user, channel=channel)

    @pytest.mark.asyncio
    async def test_host_is_not_misled_when_host_cannot_be_found(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        for channel_type in (discord.ChannelType.public_thread,
                             discord.ChannelType.private_thread):
            interaction = self._host_not_found_interaction(
                matchmaking, user=host, channel_type=channel_type)

            await Matchmaking.rename.callback(
                matchmaking, interaction, title="New Room")

            assert interaction.response.deferred is True
            assert interaction.channel.edited_kwargs is None
            assert interaction.followup.sent[0][0] == (
                "This thread cannot be renamed because the host could not be"
                " found."
            )

    @pytest.mark.asyncio
    async def test_modal_host_is_not_misled_when_host_cannot_be_found(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = self._host_not_found_interaction(
            matchmaking, user=host,
            channel_type=discord.ChannelType.public_thread)

        await matchmaking.rename_thread_modal(interaction, "New Room")

        assert interaction.response.deferred is True
        assert interaction.channel.edited_kwargs is None
        assert interaction.followup.sent[0][0] == (
            "This thread cannot be renamed because the host could not be"
            " found."
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
    async def test_join_notifies_subscribers(self, matchmaking):
        host = FakeMember(100, "Hosty")
        subscriber = FakeMember(101, "Subby")
        joiner = FakeMember(102, "Joiny")
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)
        interaction = FakeInteraction(
            user=joiner,
            guild=FakeGuild(id=1,
                            members={m.id: m for m in (host, subscriber, joiner)}),
            message=FakeMessage([embed]),
        )
        context = LFGContext(
            host=host, max_guests=4, users_to_notify={host, subscriber})

        await matchmaking.process_join(interaction, context)

        # Host gets the host variant, other subscribers the subscriber
        # variant, and the joiner is not DM'd about their own join.
        assert "A new player (Joiny) has joined your game" in host.sent_content
        assert "When the game is full" in host.sent_content
        assert ("A new player (Joiny) has joined your game"
                in subscriber.sent_content)
        assert "When the game thread starts" in subscriber.sent_content
        assert not hasattr(joiner, "sent_content")

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


class TestLfgConcurrency:
    """A concurrent press must never start the game twice."""

    def _interaction(self, message, channel, guild, user):
        return FakeInteraction(user=user, guild=guild, message=message,
                              channel=channel)

    def _lfg_message(self, host, limit):
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)
        embed.add_field(name=f"Guests (0/{limit})", value="", inline=False)
        return FakeMessage([embed])

    def _yielding_message(self, message):
        """Make the message's edit yield like a real REST call, so a second
        handler can reach and wait on the lock while the first holds it."""
        original_edit = message.edit

        async def yielding_edit(**kwargs):
            await asyncio.sleep(0)
            return await original_edit(**kwargs)

        message.edit = yielding_edit
        return message

    def _spy_on_thread_creation(self, matchmaking):
        started = []
        original = matchmaking.start_game_matches

        async def spy(*args, **kwargs):
            started.append(1)
            return await original(*args, **kwargs)

        matchmaking.start_game_matches = spy
        return started

    def _followup_messages(self, *interactions):
        return [sent[0] for interaction in interactions
                for sent in interaction.followup.sent]

    @pytest.mark.asyncio
    async def test_concurrent_joins_start_a_single_thread(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guest_a = FakeMember(101, "A")
        guest_b = FakeMember(102, "B")
        guild = FakeGuild(
            id=1, members={m.id: m for m in (host, guest_a, guest_b)})
        # One seat left: both presses would fill the game and auto-start it.
        message = self._yielding_message(self._lfg_message(host, limit=1))
        channel = FakeChannel()
        started = self._spy_on_thread_creation(matchmaking)

        interaction_a = self._interaction(message, channel, guild, guest_a)
        interaction_b = self._interaction(message, channel, guild, guest_b)
        game_option = matchmaking.default_guild_config.games["game_a"]
        context_a = LFGContext(host=host, max_guests=1, users_to_notify=set(),
                               game_option=game_option)
        context_b = LFGContext(host=host, max_guests=1, users_to_notify=set(),
                               game_option=game_option)

        await asyncio.gather(
            matchmaking.process_join(interaction_a, context_a),
            matchmaking.process_join(interaction_b, context_b),
        )

        assert len(started) == 1
        messages = self._followup_messages(interaction_a, interaction_b)
        assert messages.count("The game has started!") == 1
        assert messages.count("This game is already closed.") == 1
        assert matchmaking._lfg_locks == {}

    @pytest.mark.asyncio
    async def test_start_racing_auto_start_creates_a_single_thread(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        guest = FakeMember(101, "A")
        guild = FakeGuild(id=1, members={m.id: m for m in (host, guest)})
        message = self._yielding_message(self._lfg_message(host, limit=1))
        channel = FakeChannel()
        started = self._spy_on_thread_creation(matchmaking)

        interaction_start = self._interaction(message, channel, guild, host)
        interaction_join = self._interaction(message, channel, guild, guest)
        game_option = matchmaking.default_guild_config.games["game_a"]
        context_start = LFGContext(host=host, users_to_notify=set(),
                                   game_option=game_option)
        context_join = LFGContext(host=host, max_guests=1, users_to_notify=set(),
                                  game_option=game_option)

        await asyncio.gather(
            matchmaking.process_start(interaction_start, context_start),
            matchmaking.process_join(interaction_join, context_join),
        )

        assert len(started) == 1
        messages = self._followup_messages(interaction_start, interaction_join)
        assert messages.count("The game has started!") == 1

    @pytest.mark.asyncio
    async def test_press_on_a_closed_game_is_rejected(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guest = FakeMember(101, "A")
        guild = FakeGuild(id=1, members={m.id: m for m in (host, guest)})
        # An empty component list means the view was already removed.
        message = self._lfg_message(host, limit=4)
        message.components = []
        channel = FakeChannel()
        started = self._spy_on_thread_creation(matchmaking)

        interaction = self._interaction(message, channel, guild, guest)
        context = LFGContext(host=host, max_guests=4, users_to_notify=set())

        await matchmaking.process_join(interaction, context)

        assert interaction.response.deferred is True
        assert interaction.followup.sent[0][0] == "This game is already closed."
        assert started == []
        assert matchmaking._lfg_locks == {}

    @pytest.mark.asyncio
    async def test_lock_is_dropped_when_the_game_is_cancelled(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        message = self._lfg_message(host, limit=4)
        channel = FakeChannel()
        interaction = self._interaction(message, channel, guild, host)
        context = LFGContext(host=host, users_to_notify=set())

        await matchmaking.process_cancel(interaction, context)

        assert message.edited is not None
        assert matchmaking._lfg_locks == {}


class TestHasLfgView:
    def test_open_view_is_detected(self):
        assert has_lfg_view(FakeMessage(components=lfg_view_components()))

    def test_empty_components_means_closed(self):
        assert not has_lfg_view(FakeMessage(components=[]))

    def test_unknown_components_are_assumed_open(self):
        assert has_lfg_view(FakeMessage())


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

    @pytest.mark.asyncio
    async def test_cancel_notifies_subscribers(self, matchmaking):
        host = FakeMember(100, "Hosty")
        subscriber = FakeMember(101, "Subby")
        watcher = FakeMember(102, "Watcher")
        interaction = FakeInteraction(user=host)
        context = LFGContext(
            host=host, users_to_notify={host, subscriber, watcher})

        await matchmaking.process_cancel(interaction, context)

        # Every subscriber is told the game was cancelled, except the host
        # who cancelled it themselves.
        assert ("has been cancelled by the host (Hosty)"
                in subscriber.sent_content)
        assert ("has been cancelled by the host (Hosty)"
                in watcher.sent_content)
        assert not hasattr(host, "sent_content")
        assert interaction.followup.sent[0][0] == "The game has been canceled."


class TestNotificationMessages:
    def test_join_message_host_variant(self, matchmaking):
        joiner = FakeMember(102, "Joiny")

        message = matchmaking._join_notification_message(
            FakeChannel(id=7), joiner, is_host=True)

        assert message == (
            "A new player (Joiny) has joined your game"
            " in the LFG channel <#7>.\n"
            "When the game is full, you can start the thread using "
            + EMOJI_START + ", which will ping all the players. GLHF!")

    def test_join_message_subscriber_variant(self, matchmaking):
        joiner = FakeMember(102, "Joiny")

        message = matchmaking._join_notification_message(
            FakeChannel(id=7), joiner, is_host=False)

        assert message == (
            "A new player (Joiny) has joined your game"
            " in the LFG channel <#7>.\n"
            "When the game thread starts, you will be pinged there. GLHF!")

    def test_cancel_message(self, matchmaking):
        host = FakeMember(100, "Hosty")

        message = matchmaking._cancel_notification_message(
            FakeChannel(id=7), host)

        assert message == (
            "The game you subscribed to in the LFG channel <#7>"
            " has been cancelled by the host (Hosty). Sorry!")


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
        command = matchmaking._make_game_command("game_a", DEFAULT_GUILD_ID)
        expected = {"description", "max_players",
                    *matchmaking.game_parameters[DEFAULT_GUILD_ID]["game_a"]}
        assert expected <= set(command._params.keys())
        assert all(not param.required for param in command._params.values())
        # Every game parameter needs an autocomplete wired up.
        for param_name in matchmaking.game_parameters[DEFAULT_GUILD_ID]["game_a"]:
            assert command._params[param_name].autocomplete is not None

    def test_generated_callback_signature(self, matchmaking):
        callback = matchmaking._make_game_callback("game_a", DEFAULT_GUILD_ID)
        names = list(inspect.signature(callback).parameters)
        # The always-present arguments plus one argument per configured
        # parameter of the game (derived from the fixture config).
        assert set(names) == (
            {"interaction", "title", "description", "max_players", "nb_games"}
            | set(matchmaking.game_parameters[DEFAULT_GUILD_ID]["game_a"])
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
        autocomplete = matchmaking._make_param_autocomplete(
            DEFAULT_GUILD_ID, "game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        choices = await autocomplete(interaction, "al")
        # The choice is the full composed string (name == value), so whichever
        # the client writes into the field, the prefix is preserved.
        assert [(c.name, c.value) for c in choices] == [("Alpha One", "Alpha One")]

    @pytest.mark.asyncio
    async def test_filters_by_display_name(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete(
            DEFAULT_GUILD_ID, "game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        # Filtering matches the display name too, not just the raw value.
        choices = await autocomplete(interaction, "gamma t")
        assert [(c.name, c.value) for c in choices] == [("Gamma Three", "Gamma Three")]

    @pytest.mark.asyncio
    async def test_composes_choices_with_existing_prefix(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete(
            DEFAULT_GUILD_ID, "game_a", "param1")
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
        autocomplete = matchmaking._make_param_autocomplete(
            DEFAULT_GUILD_ID, "game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        choices = await autocomplete(interaction, "alpha, b")

        assert [(c.name, c.value) for c in choices] == [("alpha,Beta Two", "alpha,Beta Two")]

    @pytest.mark.asyncio
    async def test_composed_values_over_100_chars_are_skipped(self, matchmaking):
        long_value = "x" * 99
        matchmaking.game_parameters[DEFAULT_GUILD_ID]["game_a"] = {
            "param1": {"display_name": "param1",
                       "values": {long_value: long_value, "ok": "OK", "ok2": "OK2"}}}
        autocomplete = matchmaking._make_param_autocomplete(
            DEFAULT_GUILD_ID, "game_a", "param1")
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
    def _modal_stub(description="let's play", max_players_value=None,
                    nb_games_value=None, game_command_value=None,
                    title_value=None):
        return SimpleNamespace(
            title_value=title_value,
            description_value=description,
            max_players_value=max_players_value,
            nb_games_value=nb_games_value,
            game_command_value=game_command_value,
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
        # Direct route: deferred ephemerally (a public defer would make the
        # creation confirmation followup public too), then the LFG post.
        assert interaction.response.deferred is True
        assert interaction.channel.sent

    def test_game_modal_confirm_creates_lfg(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        confirmation = FakeInteraction(user=host, guild=guild)

        _run(matchmaking._create_lfg_from_modal(
            confirmation,
            self._modal_stub(title_value="Raid night", max_players_value=4),
            "game_a"))

        embed = confirmation.channel.sent[0][1]
        guests = [f.name for f in embed.fields if f.name.startswith("Guests")]
        # Modal max_players=4 -> 3 guests.
        assert guests == ["Guests (0/3)"]
        # The modal title lands in the embed title suffix.
        assert embed.title == "Looking for a Game A game: Raid night"

    def test_game_modal_confirm_uses_default_max_guests(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        command = FakeInteraction(user=host, guild=guild)
        confirmation = FakeInteraction(user=host, guild=guild)

        _run(matchmaking._run_game_command(command, "game_a", {}))
        modal = command.response.modals[0]
        _run(modal.on_confirm(confirmation, self._modal_stub()))

        embed = confirmation.channel.sent[0][1]
        guests = [f.name for f in embed.fields if f.name.startswith("Guests")]
        # Fixture game_a default max players = 5 -> 4 guests.
        assert guests == ["Guests (0/4)"]

    def test_guided_lfg_selection_still_shares_modal_tail(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(matchmaking.process_game_settings(
            interaction,
            self._modal_stub(max_players_value=2,
                             game_command_value="game_a")))

        embed = interaction.channel.sent[0][1]
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
        fields = matchmaking.game_api_fields[DEFAULT_GUILD_ID]["game_a"]
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
        params = matchmaking.game_parameters[DEFAULT_GUILD_ID]["game_a"]
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

        fields = matchmaking.game_api_fields[DEFAULT_GUILD_ID]["bare_game"]
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
    def _modal_stub(description="let's play", max_players_value=None,
                    nb_games_value=None, game_command_value=None,
                    title_value=None):
        return SimpleNamespace(
            title_value=title_value,
            description_value=description,
            max_players_value=max_players_value,
            nb_games_value=nb_games_value,
            game_command_value=game_command_value,
        )

    def test_game_only_opens_settings_modal(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(matchmaking, interaction, game="game_a"))

        assert len(interaction.response.modals) == 1
        assert isinstance(interaction.response.modals[0], GameSettingsModal)
        # The modal is the response; nothing else was sent.
        assert interaction.response.messages == []
        # The game is already known: no select, just the four inputs.
        modal = interaction.response.modals[0]
        assert modal.game_select is None
        assert len(modal.children) == 4

    def test_game_with_settings_goes_direct(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(
            matchmaking, interaction, game="game_a", description="hi"))

        assert interaction.response.modals == []
        # Direct route: deferred ephemerally (a public defer would make the
        # creation confirmation followup public too), then the LFG post.
        assert interaction.response.deferred is True
        assert interaction.channel.sent

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
        _run(modal.on_confirm(confirmation, self._modal_stub()))

        embed = confirmation.channel.sent[0][1]
        guests = [f.name for f in embed.fields if f.name.startswith("Guests")]
        # Fixture game_a default max players = 5 -> 4 guests.
        assert guests == ["Guests (0/4)"]

    def test_direct_settings_without_game_still_rejected(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(matchmaking, interaction, max_players=4))

        assert "The `game` argument is required" in interaction.response.messages[0][0]


class TestLfgGuidedModal:
    """/lfg without arguments: the modal itself holds the game select."""

    def test_guided_lfg_opens_a_modal_with_a_game_select(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Host"), guild_id=1)

        _run(Matchmaking.lfg.callback(matchmaking, interaction))

        assert len(interaction.response.modals) == 1
        # The modal is the response; nothing else was sent.
        assert interaction.response.messages == []
        modal = interaction.response.modals[0]
        # Game select + the four settings inputs, within Discord's cap.
        assert len(modal.children) == 5
        # The select must be Label-wrapped: it is the only modal-supported
        # format (a bare action-row select is rejected by the API).
        game_label = modal.children[0]
        assert game_label.type == discord.ComponentType.label
        assert game_label.component is modal.game_select
        select = modal.game_select
        assert select.required is True
        assert select.placeholder == "Select a game option..."
        expected = [(option.name, option.command) for option
                    in matchmaking.default_guild_config.games.values()]
        assert [(option.label, option.value)
                for option in select.options] == expected


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
        for param_name, parameter in matchmaking.game_parameters[DEFAULT_GUILD_ID]["game_a"].items():
            assert f"- `{param_name}`: {', '.join(parameter['values'].values())}" in description

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
            interaction, game_option, None, "desc", None,
            game_settings={"param1": ["alpha", "delta"], "param2": ["first"]},
        )

        embed = interaction.channel.sent[0][1]
        field_names = [field.name for field in embed.fields]
        assert "Settings" in field_names
        settings_value = [
            field.value for field in embed.fields if field.name == "Settings"
        ][0]
        # Raw values in the settings dict are rendered with their display names.
        assert "param1: Alpha One, Delta Four" in settings_value
        assert "param2: First Choice" in settings_value
class TestCreateLfgChannel:
    """GamesChannels: the LFG post goes to the game's configured channel.

    Without a configured channel the post stays in the channel the command
    was used in; with one it goes to the resolved channel (falling back to
    the command's channel when the target cannot be found).
    """

    @pytest.mark.asyncio
    async def test_posts_to_configured_channel(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        interaction = FakeInteraction(user=host, guild=guild)
        lfg_channel = FakeChannel(id=777, name="lfg")
        matchmaking.bot._channels[777] = lfg_channel
        # Fixture game_a has GamesChannels = <#777>.
        game_option = matchmaking.default_guild_config.games["game_a"]

        await matchmaking.create_lfg(interaction, game_option, None, "desc", None)

        # The post went to the configured LFG channel, not the command's.
        assert interaction.channel.sent == []
        assert lfg_channel.sent
        content, embed, view = lfg_channel.sent[0]
        assert content == game_option.role
        assert "Looking for" in embed.title
        assert view is not None

    @pytest.mark.asyncio
    async def test_unknown_channel_falls_back_to_command_channel(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        interaction = FakeInteraction(user=host, guild=guild)
        # Fixture game_a points at <#777>, which is not registered on the bot.
        game_option = matchmaking.default_guild_config.games["game_a"]

        await matchmaking.create_lfg(interaction, game_option, None, "desc", None)

        # The un-resolvable target silently falls back to the command channel.
        assert interaction.channel.sent

    @pytest.mark.asyncio
    async def test_without_channel_uses_command_channel(self, matchmaking):
        host = FakeMember(100, "Host")
        guild = FakeGuild(id=1, members={100: host})
        interaction = FakeInteraction(user=host, guild=guild)
        # Fixture game_b has a blank GamesChannels entry. Its string color
        # would trip discord.Embed.colour (a pre-existing limitation
        # unrelated to channels), so clear it for this test.
        game_option = matchmaking.default_guild_config.games["game_b"]
        game_option.color = ""

        await matchmaking.create_lfg(interaction, game_option, None, "desc", None)

        assert interaction.channel.sent
class TestNumberOfGames:
    """The nb_games option: one thread (and match) per game.

    The value is part of the LFG post's persisted state (a Games embed
    field), so it survives the context rebuild of every button press and
    drives how many threads are created when the game starts.
    """

    def _game_option(self, matchmaking):
        return matchmaking.default_guild_config.games["game_a"]

    def _embed(self, host, nb_games=None):
        embed = discord.Embed(title="Looking for a Game A game",
                              description="Game A night")
        embed.add_field(name="Host", value=host.mention, inline=True)
        if (nb_games is not None):
            embed.add_field(name=LFG_FIELD_GAMES, value=str(nb_games),
                            inline=True)
        return embed

    def _lfg_message(self, host, nb_games=None):
        return FakeMessage([self._embed(host, nb_games)])

    def _thread_factory(self, created, forum=False):
        """A create_thread replacement recording the threads it hands out."""
        def factory(**kwargs):
            thread = FakeThread(name=kwargs.get("name"),
                                id=5000 + len(created))
            created.append(thread)
            if (forum):
                return (thread, FakeMessage())
            return thread
        return factory

    def _games_field(self, embed):
        return next((field for field in embed.fields
                     if field.name == LFG_FIELD_GAMES), None)

    @pytest.mark.asyncio
    async def test_single_game_post_has_no_games_field(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await matchmaking.create_lfg(
            interaction, self._game_option(matchmaking), None, "desc", None)

        embed = interaction.channel.sent[0][1]
        assert self._games_field(embed) is None

    @pytest.mark.asyncio
    async def test_multi_game_post_records_the_nb_games(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await matchmaking.create_lfg(
            interaction, self._game_option(matchmaking), None, "desc", None,
            nb_games=3)

        embed = interaction.channel.sent[0][1]
        assert self._games_field(embed).value == "3"

    @pytest.mark.asyncio
    async def test_context_recovers_the_nb_games(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}),
            message=self._lfg_message(host, nb_games=4))

        context = await LFGContext.from_interaction(matchmaking, interaction)

        assert context.nb_games == 4

    @pytest.mark.asyncio
    async def test_context_defaults_to_a_single_game(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}),
            message=self._lfg_message(host))

        context = await LFGContext.from_interaction(matchmaking, interaction)

        assert context.nb_games == DEFAULT_NB_GAMES

    @pytest.mark.asyncio
    async def test_unparsable_games_field_falls_back_to_one(self, matchmaking):
        host = FakeMember(100, "Hosty")
        embed = self._embed(host)
        embed.add_field(name=LFG_FIELD_GAMES, value="squad", inline=True)
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}),
            message=FakeMessage([embed]))

        context = await LFGContext.from_interaction(matchmaking, interaction)

        assert context.nb_games == DEFAULT_NB_GAMES

    @pytest.mark.asyncio
    async def test_out_of_range_games_field_is_capped(self, matchmaking):
        host = FakeMember(100, "Hosty")
        embed = self._embed(host)
        embed.add_field(name=LFG_FIELD_GAMES, value="99", inline=True)
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}),
            message=FakeMessage([embed]))

        context = await LFGContext.from_interaction(matchmaking, interaction)

        assert context.nb_games == MAX_NB_GAMES

    @pytest.mark.asyncio
    async def test_join_preserves_the_games_field(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guest = FakeMember(101, "G")
        guild = FakeGuild(id=1, members={100: host, 101: guest})
        message = self._lfg_message(host, nb_games=3)
        interaction = FakeInteraction(
            user=guest, guild=guild, message=message, channel=FakeChannel())
        context = await LFGContext.from_interaction(matchmaking, interaction)

        await matchmaking.process_join(interaction, context)

        updated_embed = message.edited["embed"]
        assert self._games_field(updated_embed).value == "3"

    @pytest.mark.asyncio
    async def test_start_creates_one_thread_per_game(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        message = self._lfg_message(host, nb_games=3)
        channel = FakeChannel()
        created = []
        channel.thread_factory = self._thread_factory(created)
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=channel)
        context = await LFGContext.from_interaction(matchmaking, interaction)

        await matchmaking.process_start(interaction, context)

        assert [thread.name for thread in created] == [
            "(1/3) Game A night", "(2/3) Game A night", "(3/3) Game A night"]
        # Every game's thread pings the players.
        assert all(thread.sent for thread in created)
        # The first thread inherits the post's embed from the LFG message it
        # attaches to; the standalone extras carry their own copy instead.
        assert created[0].sent_embeds == [None]
        for extra in created[1:]:
            assert len(extra.sent_embeds) == 1
            assert extra.sent_embeds[0].url == message.jump_url

    @pytest.mark.asyncio
    async def test_single_game_keeps_the_plain_thread_name(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        message = self._lfg_message(host)
        channel = FakeChannel()
        created = []
        channel.thread_factory = self._thread_factory(created)
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=channel)
        context = await LFGContext.from_interaction(matchmaking, interaction)

        await matchmaking.process_start(interaction, context)

        assert [thread.name for thread in created] == ["Game A night"]

    @pytest.mark.asyncio
    async def test_extra_games_become_standalone_threads(self, matchmaking):
        # A message holds a single thread: only the first game attaches to
        # the LFG message, the others are standalone public threads.
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        message = self._lfg_message(host, nb_games=3)
        channel = FakeChannel()
        created = []
        channel.thread_factory = self._thread_factory(created)
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=channel)
        context = await LFGContext.from_interaction(matchmaking, interaction)

        await matchmaking.process_start(interaction, context)

        requests = channel.created_thread_kwargs
        assert len(requests) == 3
        assert requests[0]["message"] is message
        for extra in requests[1:]:
            assert "message" not in extra
            assert extra["type"] == discord.ChannelType.public_thread

    @pytest.mark.asyncio
    async def test_forum_gets_one_post_per_game(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        forum = FakeChannel(id=555, name="game-a-forum",
                            type_=discord.ChannelType.forum)
        matchmaking.bot._channels = {555: forum}
        game_option = self._game_option(matchmaking)
        game_option.forum = "<#555>"
        created = []
        forum.thread_factory = self._thread_factory(created, forum=True)
        message = self._lfg_message(host, nb_games=2)
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=FakeChannel())
        context = LFGContext(game_option=game_option, host=host,
                             nb_games=2)

        await matchmaking.start_game_matches(interaction, context)

        assert len(created) == 2
        assert [request["name"] for request in forum.created_thread_kwargs] == [
            "(1/2) Game A night", "(2/2) Game A night"]
        # The LFG post links to the first game's thread.
        assert message.edited["embed"].url == created[0].jump_url

    @pytest.mark.asyncio
    async def test_each_game_registers_its_own_match(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        channel = FakeChannel()
        created = []
        channel.thread_factory = self._thread_factory(created)
        game_option = self._game_option(matchmaking)
        game_option.match_api = "https://site/api/matches/"
        message = self._lfg_message(host, nb_games=3)
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=channel)
        context = LFGContext(game_option=game_option, host=host,
                             nb_games=3)
        registered = []

        async def fake_register(thread, match_api_url, match_url, auth_token,
                                title, website_name, verified_users, **kwargs):
            registered.append((thread, title))

        matchmaking.register_match = fake_register

        await matchmaking.start_game_matches(interaction, context)

        # Each game is registered with its own thread link and title.
        assert [thread for thread, _ in registered] == created
        assert [title for _, title in registered] == [
            "(1/3) Game A night", "(2/3) Game A night", "(3/3) Game A night"]

    @pytest.mark.asyncio
    async def test_lfg_command_starts_several_games(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await Matchmaking.lfg.callback(
            matchmaking, interaction, game="game_a", nb_games=3)

        embed = interaction.channel.sent[0][1]
        assert self._games_field(embed).value == "3"

    @pytest.mark.asyncio
    async def test_nb_games_selects_the_direct_route(self, matchmaking):
        # Like the other LFG arguments (description, max_players), providing
        # the number of games as an argument skips the modal; the modal route
        # stays available with just the game argument.
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await Matchmaking.lfg.callback(
            matchmaking, interaction, game="game_a", nb_games=2)

        assert interaction.response.modals == []
        assert interaction.channel.sent

    @pytest.mark.asyncio
    async def test_out_of_range_nb_games_is_rejected(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        # /lfg caps the option at the Discord level, but the per-game
        # commands declare it as a plain integer.
        await matchmaking._direct_lfg(interaction, "game_a", None, None,
                                      None, nb_games=99)

        assert ("`nb_games` must be between 1 and 10"
                in interaction.response.messages[0][0])
        assert interaction.channel.sent == []

    @pytest.mark.asyncio
    async def test_game_command_passes_nb_games(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await matchmaking._run_game_command(
            interaction, "game_a", {"nb_games": 2})

        assert interaction.response.modals == []
        embed = interaction.channel.sent[0][1]
        assert self._games_field(embed).value == "2"

    def test_modal_holds_the_lfg_arguments(self):
        # The modal holds the LFG arguments (well within Discord's 5-component
        # cap); the per-game settings stay command-argument only, since their
        # number is not bounded.
        modal = GameSettingsModal()
        labels = [child.to_component_dict()["label"]
                  for child in modal.children]

        assert labels == [
            "Title", "Description", "Max number of players (2-100)",
            "Number of games (1-10)"]
        assert len(modal.children) <= 5

    def test_modal_accepts_the_nb_games(self):
        modal = GameSettingsModal()
        modal.nb_games_input._value = "3"

        _run(modal.on_submit(FakeInteraction(user=FakeMember(1, "Hosty"))))

        assert modal.nb_games_value == 3

    def test_modal_rejects_an_out_of_range_nb_games(self):
        async def on_confirm(*args):
            raise AssertionError("an invalid modal must not confirm")

        modal = GameSettingsModal(on_confirm=on_confirm)
        modal.nb_games_input._value = "99"
        interaction = FakeInteraction(user=FakeMember(1, "Hosty"))

        _run(modal.on_submit(interaction))

        assert modal.nb_games_value is None
        assert ("number of games from 1 to 10"
                in interaction.response.messages[0][0])

    @pytest.mark.asyncio
    async def test_modal_confirm_posts_the_nb_games(self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        confirmation = FakeInteraction(user=host, guild=guild)
        modal = SimpleNamespace(
            title_value=None,
            description_value="Game A night",
            max_players_value=None,
            nb_games_value=3,
        )

        await matchmaking._create_lfg_from_modal(confirmation, modal, "game_a")

        embed = confirmation.channel.sent[0][1]
        assert self._games_field(embed).value == "3"

    @pytest.mark.asyncio
    async def test_guided_modal_nb_games_reaches_the_post(self, matchmaking):
        # End to end through the real modal: /lfg -> modal (game select +
        # settings) -> LFG post carrying the requested number of games.
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        command = FakeInteraction(user=host, guild=guild)

        await Matchmaking.lfg.callback(matchmaking, command)

        modal = command.response.modals[0]
        modal.game_select._values = ["game_a"]
        modal.nb_games_input._value = "3"
        confirmation = FakeInteraction(user=host, guild=guild)

        await modal.on_submit(confirmation)

        embed = confirmation.channel.sent[0][1]
        assert self._games_field(embed).value == "3"

    @pytest.mark.asyncio
    async def test_long_titles_keep_the_game_prefix(self, matchmaking):
        # The cap keeps the beginning of the name: the (i/n) prefix must
        # survive a description long enough to hit the 100-character limit.
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        embed = discord.Embed(title="Looking for a Game A game",
                              description="x" * 120)
        embed.add_field(name="Host", value=host.mention, inline=True)
        embed.add_field(name=LFG_FIELD_GAMES, value="3", inline=True)
        message = FakeMessage([embed])
        channel = FakeChannel()
        created = []
        channel.thread_factory = self._thread_factory(created)
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=channel)
        context = await LFGContext.from_interaction(matchmaking, interaction)

        await matchmaking.process_start(interaction, context)

        expected = [f"({index}/3) " + "x" * 94 for index in (1, 2, 3)]
        assert [thread.name for thread in created] == expected


class TestLfgTitle:
    """The optional LFG title: embed-title suffix, thread title, and limits."""

    def _embed(self, title, description=None):
        host = FakeMember(100, "Hosty")
        embed = discord.Embed(title=title, description=description)
        embed.add_field(name="Host", value=host.mention, inline=True)
        return embed

    def test_embed_title_without_title_keeps_the_legacy_format(self):
        from cogs.matchmaking import utils as mm_utils

        assert mm_utils.embed_title("Game A") == "Looking for a Game A game"
        assert mm_utils.embed_title("Apple") == "Looking for an Apple game"

    def test_embed_title_appends_the_title_suffix(self):
        from cogs.matchmaking import utils as mm_utils

        assert mm_utils.embed_title("Game A", "Raid night") == (
            "Looking for a Game A game: Raid night")

    def test_embed_title_suffix_is_skipped_for_game_colon_names(self):
        # "Quiz game: Night" would parse back as "Quiz": the suffix must not
        # be appended for such names.
        from cogs.matchmaking import utils as mm_utils

        assert mm_utils.embed_title("Quiz game: Night", "Raid") == (
            "Looking for a Quiz game: Night game")

    def test_embed_title_suffix_is_skipped_for_oversized_names(self):
        from cogs.matchmaking import utils as mm_utils, constants as mm_constants

        long_name = "G" * (mm_constants.GAME_NAME_MAX + 1)
        title = mm_utils.embed_title(long_name, "Raid")
        assert title == f"Looking for a {long_name} game"

    def test_embed_title_is_always_within_the_discord_limit(self):
        from cogs.matchmaking import utils as mm_utils, constants as mm_constants

        # Worst case allowed by the command's Range caps.
        title = mm_utils.embed_title("G" * mm_constants.GAME_NAME_MAX,
                                     "T" * mm_constants.LFG_TITLE_MAX)
        assert len(title) <= mm_constants.LFG_EMBED_TITLE_MAX

    def test_game_recovery_round_trips_through_the_title_suffix(self):
        from cogs.matchmaking import constants as mm_constants

        title = "Looking for a Board game game: Friday night"
        game_name = mm_constants.LFG_TITLE_RE.search(title).group(1)
        assert game_name == "Board game"

    def test_lfg_title_from_embed(self):
        from cogs.matchmaking import utils as mm_utils

        assert mm_utils.lfg_title_from_embed(
            self._embed("Looking for a Game A game: Raid night")) == "Raid night"
        # Legacy posts carry no suffix.
        assert mm_utils.lfg_title_from_embed(
            self._embed("Looking for a Game A game")) is None

    def test_thread_title_prefers_the_lfg_title_over_the_description(self):
        from cogs.matchmaking import utils as mm_utils

        embed = self._embed("Looking for a Game A game: Raid night",
                            description="Game A night")
        assert mm_utils.thread_title_from_embed(embed) == "Raid night"

    def test_thread_title_falls_back_to_the_description(self):
        from cogs.matchmaking import utils as mm_utils

        embed = self._embed("Looking for a Game A game",
                            description="Game A night")
        assert mm_utils.thread_title_from_embed(embed) == "Game A night"

    def test_thread_title_final_fallbacks(self):
        from cogs.matchmaking import utils as mm_utils

        embed = self._embed("Looking for a Game A game")
        assert mm_utils.thread_title_from_embed(embed) == (
            "Looking for a Game A game")
        empty = self._embed("")
        assert mm_utils.thread_title_from_embed(empty) == "Game thread"

    def test_thread_title_is_capped_at_100(self):
        from cogs.matchmaking import utils as mm_utils

        embed = self._embed("Looking for a Game A game: " + "T" * 120)
        assert mm_utils.thread_title_from_embed(embed) == "T" * 100

    @pytest.mark.asyncio
    async def test_direct_lfg_title_reaches_the_embed(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await matchmaking._direct_lfg(
            interaction, "game_a", "Raid night", "the description", None)

        embed = interaction.channel.sent[0][1]
        assert embed.title == "Looking for a Game A game: Raid night"
        assert embed.description == "the description"

    @pytest.mark.asyncio
    async def test_lfg_command_with_title_only_goes_direct(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await Matchmaking.lfg.callback(
            matchmaking, interaction, game="game_a", title="Raid night")

        # A title alone counts as direct settings: no modal, direct post.
        assert interaction.response.modals == []
        assert interaction.response.deferred is True
        embed = interaction.channel.sent[0][1]
        assert embed.title == "Looking for a Game A game: Raid night"

    @pytest.mark.asyncio
    async def test_lfg_title_without_game_is_rejected(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(100, "Hosty"), guild_id=1)

        await Matchmaking.lfg.callback(
            matchmaking, interaction, title="Raid night")

        assert ("The `game` argument is required"
                in interaction.response.messages[0][0])
        assert interaction.channel.sent == []

    @pytest.mark.asyncio
    async def test_per_game_command_passes_the_title(self, matchmaking):
        host = FakeMember(100, "Hosty")
        interaction = FakeInteraction(
            user=host, guild=FakeGuild(id=1, members={100: host}))

        await matchmaking._run_game_command(
            interaction, "game_a", {"title": "Raid night"})

        embed = interaction.channel.sent[0][1]
        assert embed.title == "Looking for a Game A game: Raid night"

    @pytest.mark.asyncio
    async def test_started_post_uses_the_title_for_the_thread(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        embed = self._embed("Looking for a Game A game: Raid night",
                            description="Game A night")
        message = FakeMessage([embed])
        channel = FakeChannel()
        created = []

        def thread_factory(**kwargs):
            thread = FakeThread(id=900 + len(created), name=kwargs.get("name"))
            created.append(thread)
            return thread

        channel.thread_factory = thread_factory
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=channel)
        context = await LFGContext.from_interaction(matchmaking, interaction)

        await matchmaking.process_start(interaction, context)

        # The thread gets the title, not the description.
        assert created
        assert created[0].name == "Raid night"

    @pytest.mark.asyncio
    async def test_started_legacy_post_still_uses_the_description(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        guild = FakeGuild(id=1, members={100: host})
        embed = self._embed("Looking for a Game A game",
                            description="Game A night")
        message = FakeMessage([embed])
        channel = FakeChannel()
        created = []

        def thread_factory(**kwargs):
            thread = FakeThread(id=900 + len(created), name=kwargs.get("name"))
            created.append(thread)
            return thread

        channel.thread_factory = thread_factory
        interaction = FakeInteraction(
            user=host, guild=guild, message=message, channel=channel)
        context = await LFGContext.from_interaction(matchmaking, interaction)

        await matchmaking.process_start(interaction, context)

        assert created
        assert created[0].name == "Game A night"


class TestGameNameValidation:
    """Games add and update name caps keeping the embed title parsable."""

    def test_long_name_is_rejected(self):
        from cogs.matchmaking.admin import LFGAdminMixin

        assert LFGAdminMixin._game_name_error("G" * 101) is not None
        assert LFGAdminMixin._game_name_error("G" * 100) is None

    def test_game_colon_name_is_rejected(self):
        from cogs.matchmaking.admin import LFGAdminMixin

        assert LFGAdminMixin._game_name_error("Quiz game: Night") is not None
        assert LFGAdminMixin._game_name_error("Quiz Game: Night") is not None
        assert LFGAdminMixin._game_name_error("Game Night") is None
        assert LFGAdminMixin._game_name_error("") is None
        assert LFGAdminMixin._game_name_error(None) is None

    def test_games_add_rejects_an_invalid_name(self):
        from cogs.matchmaking.admin import LFGAdminMixin

        fields, error = LFGAdminMixin._game_fields(
            name="Quiz game: Night", role="", icon="", color="")
        assert fields is None
        assert "game:" in error

    def test_updated_fields_rejects_an_invalid_name(self):
        from cogs.matchmaking.admin import LFGAdminMixin

        fields, error = LFGAdminMixin._updated_fields(name="Quiz game: Night")
        assert fields is None
        assert "game:" in error

    def test_updated_fields_do_not_reset_the_name_with_the_sentinel(self):
        from cogs.matchmaking.admin import LFGAdminMixin

        # Unlike api_token, the name has no "-" reset: a game always has a
        # display name, so the sentinel is just an (invalid) name.
        fields, error = LFGAdminMixin._updated_fields(name="-")
        assert error is not None


class TestConfigGameNameCaps:
    """Config-file game names are capped and flagged at load time."""

    def _load(self, names_line):
        import configparser as cp
        from cogs.matchmaking.config import LFGConfigMixin
        from cogs.matchmaking.models import GuildGamesConfig

        config = cp.ConfigParser()
        config.read_string(
            "[DEFAULT]\nID = 0\n"
            "[Guild]\nID = 1\n"
            f"GamesCommands = game_a\nGamesFullNames = {names_line}\n"
        )
        guild_config = GuildGamesConfig(1)
        LFGConfigMixin._load_guild_config(guild_config, config, "Guild")
        return guild_config.games["game_a"]

    def test_long_name_is_capped(self):
        game = self._load("G" * 120)
        assert len(game.name) == 100

    def test_short_name_is_unchanged(self):
        game = self._load("Game A")
        assert game.name == "Game A"

    def test_game_colon_name_is_kept_but_flagged(self):
        # Kept (create_lfg skips the suffix for it); only a print warning.
        game = self._load("Quiz game: Night")
        assert game.name == "Quiz game: Night"


class TestNotifyEmbedOrder:
    """Notify rebuilds the fields so the order stays canonical:
    Target, Host, Games, Guests, Subscribed, Settings."""

    def _embed_with_settings(self, host):
        embed = discord.Embed(title="Looking for a Game A game")
        embed.add_field(name="Host", value=host.mention, inline=True)
        embed.add_field(name="Guests (0/4)", value="", inline=False)
        embed.add_field(name="Settings", value="param1: Alpha", inline=False)
        return embed

    @pytest.mark.asyncio
    async def test_subscribe_puts_subscribed_before_settings(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        subscriber = FakeMember(101, "Subby")
        guild = FakeGuild(id=1, members={100: host, 101: subscriber})
        message = FakeMessage([self._embed_with_settings(host)])
        interaction = FakeInteraction(
            user=subscriber, guild=guild, message=message)
        context = LFGContext(host=host, max_guests=4, users_to_notify=set(),
                             game_settings={"param1": ["alpha"]})

        await matchmaking.process_notify(interaction, context)

        names = [field.name for field in message.embeds[0].fields]
        assert names == ["Host", "Guests (0/4)", "Subscribed", "Settings"]

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_subscribed_and_keeps_settings_last(
            self, matchmaking):
        host = FakeMember(100, "Hosty")
        subscriber = FakeMember(101, "Subby")
        guild = FakeGuild(id=1, members={100: host, 101: subscriber})
        message = FakeMessage([self._embed_with_settings(host)])
        interaction = FakeInteraction(
            user=subscriber, guild=guild, message=message)
        context = LFGContext(
            host=host, max_guests=4, users_to_notify={subscriber},
            game_settings={"param1": ["alpha"]})

        await matchmaking.process_notify(interaction, context)

        names = [field.name for field in message.embeds[0].fields]
        assert names == ["Host", "Guests (0/4)", "Settings"]

    @pytest.mark.asyncio
    async def test_toggle_cycle_keeps_the_canonical_order(self, matchmaking):
        host = FakeMember(100, "Hosty")
        subscriber = FakeMember(101, "Subby")
        guild = FakeGuild(id=1, members={100: host, 101: subscriber})
        message = FakeMessage([self._embed_with_settings(host)])
        context = LFGContext(host=host, max_guests=4, users_to_notify=set(),
                             game_settings={"param1": ["alpha"]})

        for expected in (["Host", "Guests (0/4)", "Subscribed", "Settings"],
                         ["Host", "Guests (0/4)", "Settings"],
                         ["Host", "Guests (0/4)", "Subscribed", "Settings"]):
            interaction = FakeInteraction(
                user=subscriber, guild=guild, message=message)
            await matchmaking.process_notify(interaction, context)
            names = [field.name for field in message.embeds[0].fields]
            assert names == expected
