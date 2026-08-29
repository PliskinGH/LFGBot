"""Tests for the ``cogs/matchmaking.py`` cog."""
import asyncio
import configparser
import inspect
from types import SimpleNamespace

import discord
import pytest

from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.constants import LFG_COMMAND, RENAME_COMMAND
from cogs.matchmaking.models import LFGContext
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

        content = interaction.response.messages[0][0]
        assert f"# Help: /{LFG_COMMAND}" in content
        assert f"`/{LFG_COMMAND} game:<game>" in content
        assert "- `game_a`" in content
        assert "<@&111>" in content
        assert "No games are configured" not in content

    def test_rename_help_includes_usage(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, RENAME_COMMAND))

        content = interaction.response.messages[0][0]
        assert f"# Help: /{RENAME_COMMAND}" in content
        assert f"`/{RENAME_COMMAND} title:<new title>`" in content
        assert f"`/{RENAME_COMMAND}` without arguments" in content

    def test_game_command_help_signals_alias_and_pastes_lfg_help(self, matchmaking):
        # game_b has no configured parameters, so its help is exactly the /lfg
        # help (parametrized games additionally get a parameters section,
        # covered in TestGameParametersHelp).
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, "game_b"))

        content = interaction.response.messages[0][0]
        assert "# Help: /game_b" in content
        assert f"`/game_b` is a shortcut for `/{LFG_COMMAND} game:game_b`" in content

        # Everything after the alias note is the exact /lfg help body.
        lfg_interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(lfg_interaction, LFG_COMMAND))
        lfg_content = lfg_interaction.response.messages[0][0]
        assert content.endswith(lfg_content.split("\n", 1)[1])
        assert "## Available games" in content

    def test_unknown_topic_falls_back_to_generic_help(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, "unknown"))
        content = interaction.response.messages[0][0]
        assert "# Help: /unknown" in content
        assert "# Help: /unknown" in content
        assert "No detailed help is available" in content


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
        embed.add_field(name="Settings", value="param1: alpha, delta\nparam2: first", inline=False)

        message = FakeMessage([embed])
        interaction = FakeInteraction(
            user=guest, guild=guild, message=message, channel=FakeChannel()
        )

        # Reconstruct the context exactly as a button press would, then join.
        context = await LFGContext.from_interaction(matchmaking, interaction)
        assert context.game_settings == {"param1": ["alpha", "delta"], "param2": ["first"]}

        await matchmaking.process_join(interaction, context)

        updated_embed = message.edited["embed"]
        field_names = [field.name for field in updated_embed.fields]
        assert "Settings" in field_names
        settings_field = next(
            field for field in updated_embed.fields if field.name == "Settings"
        )
        assert "param1: alpha, delta" in settings_field.value
        assert "param2: first" in settings_field.value


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
        assert [(c.name, c.value) for c in choices] == [("alpha", "alpha")]

    @pytest.mark.asyncio
    async def test_composes_choices_with_existing_prefix(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        # Discord replaces the whole field with the picked choice's value, so
        # after a comma the choices carry the existing picks as a prefix:
        # picking one appends it instead of overwriting it. Both name and value
        # are the composed string, because some clients commit the choice by
        # writing its name; making them identical preserves the prefix either
        # way. The already-present value is not suggested again.
        choices = await autocomplete(interaction, "beta,")

        composed = {"beta,alpha", "beta,gamma", "beta,delta"}
        assert {c.value for c in choices} == composed
        assert {c.name for c in choices} == composed

    @pytest.mark.asyncio
    async def test_prefix_with_trailing_space(self, matchmaking):
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        choices = await autocomplete(interaction, "alpha, b")

        assert [(c.name, c.value) for c in choices] == [("alpha,beta", "alpha,beta")]

    @pytest.mark.asyncio
    async def test_composed_values_over_100_chars_are_skipped(self, matchmaking):
        long_value = "x" * 99
        matchmaking.game_parameters["game_a"] = {"param1": [long_value, "ok", "ok2"]}
        autocomplete = matchmaking._make_param_autocomplete("game_a", "param1")
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        # Without a prefix the 99-char value still fits the 100-char cap.
        choices = await autocomplete(interaction, "")
        assert {c.value for c in choices} == {long_value, "ok", "ok2"}

        # With a prefix, composing the 99-char value would exceed the cap, so
        # only the short values are offered.
        choices = await autocomplete(interaction, "ok,")
        assert {c.value for c in choices} == {"ok,ok2"}


class TestParamParsing:
    ACCEPTED = ["alpha", "beta", "gamma", "delta"]

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

        content = interaction.response.messages[0][0]
        assert "## Game parameters" in content
        assert "`/game_a` also accepts these arguments" in content
        assert "only available as command arguments" in content
        # Every configured parameter is listed with its accepted values
        # (derived from the fixture config).
        for param_name, values in matchmaking.game_parameters["game_a"].items():
            assert f"- `{param_name}`: {', '.join(values)}" in content
        # The parameters section comes after the shared /lfg help body.
        assert content.index("## Available games") < content.index("## Game parameters")

    def test_game_without_parameters_has_no_section(self, game_parameters_config):
        matchmaking = self._matchmaking_with_games(game_parameters_config)
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        _run(matchmaking.send_help(interaction, "game_b"))

        content = interaction.response.messages[0][0]
        assert "## Game parameters" not in content
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
        assert "param1: alpha, delta" in settings_value