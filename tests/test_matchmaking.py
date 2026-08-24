"""Tests for the ``cogs/matchmaking.py`` cog."""
import asyncio

import discord
import pytest

from cogs.matchmaking import LFG_COMMAND, LFGContext, Matchmaking

from tests.conftest import (
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
        assert set(games.keys()) == {"root", "oath"}

        root = games["root"]
        assert root.name == "Root"
        assert root.role == "<@&111>"
        assert root.icon == ""  # missing icon falls back to default at render time
        assert root.default_max_guests == 4
        assert root.message == ""  # empty per config; index 1 holds the message

        oath = games["oath"]
        assert oath.name == "Oath"
        assert oath.icon == "https://example.com/icon.png"
        # Oath inherits GamesMaxPlayers=2 from DEFAULT -> default max guests = 1.
        assert oath.default_max_guests == 1
        assert oath.message == "Please check the rules."

    def test_guild_specific_section_overrides_default(self, matchmaking):
        guild_config = matchmaking.guilds[90401]
        assert guild_config.guild_id == 90401
        live = guild_config.games["live"]
        assert live.name == "Live Root"
        assert live.role == "<@&333>"
        assert live.default_max_guests == 3

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
        assert "live" in guild_config.games


class TestSendHelp:
    def test_lfg_help_includes_usage_and_available_games(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, LFG_COMMAND))

        content = interaction.response.messages[0][0]
        assert "# Help: /lfg" in content
        assert "`/lfg game:<game>" in content
        assert "- `root`" in content
        assert "<@&111>" in content
        assert "No games are configured" not in content

    def test_unknown_topic_falls_back_to_generic_help(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        _run(matchmaking.send_help(interaction, "unknown"))
        content = interaction.response.messages[0][0]
        assert "# Help: /unknown" in content
        assert "No detailed help is available" in content


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

        embed = discord.Embed(title="Looking for a Root game", description="Desc")
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

        assert context.game_option == matchmaking.default_guild_config.games["root"]
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
        choices = await matchmaking.game_autocomplete(interaction, "ro")
        assert [choice.value for choice in choices] == ["root"]

    @pytest.mark.asyncio
    async def test_matches_partial_case_insensitive(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await matchmaking.game_autocomplete(interaction, "OA")
        assert [choice.value for choice in choices] == ["oath"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        assert await matchmaking.game_autocomplete(interaction, "unreal") == []

    @pytest.mark.asyncio
    async def test_uses_guild_specific_games(self, matchmaking):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=90401)
        choices = await matchmaking.game_autocomplete(interaction, "")
        assert [choice.value for choice in choices] == ["live"]


class TestProcessJoin:
    def _context(self, host, **kwargs):
        return LFGContext(host=host, max_guests=4, users_to_notify=set(), **kwargs)

    @pytest.mark.asyncio
    async def test_host_cannot_join_own_game(self, matchmaking):
        host = FakeMember(100, "Hosty")
        embed = discord.Embed(title="Looking for a Root game")
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
        embed = discord.Embed(title="Looking for a Root game")
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

        embed = discord.Embed(title="Looking for a Root game")
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
        embed = discord.Embed(title="Looking for a Root game")
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