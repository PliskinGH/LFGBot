"""Tests for the panel's Discord reads, through a REST-only client."""

import discord
import pytest

from admin_web import discord_reads

from tests.conftest import (ApiChannel, ApiGuild, ApiMember, ApiRole, ApiTag,
                            api_client, api_guild)


def _reading_guild() -> ApiGuild:
    """The guild the read tests use: one of each kind of channel and role."""
    return api_guild(
        7, "Server Seven",
        channels=[ApiChannel(10, "general", discord.ChannelType.text),
                  ApiChannel(11, "news", discord.ChannelType.news),
                  ApiChannel(12, "announce-forum", discord.ChannelType.forum),
                  ApiChannel(13, "voice", discord.ChannelType.voice)],
        roles=[ApiRole(20, "Raid"),
               ApiRole(21, "everyone", default=True),
               ApiRole(22, "Bot Role", managed=True)])


def _reads(client=None) -> discord_reads.DiscordReads:
    """Reads over a stand-in client: no network, and no state shared with another."""
    reads = discord_reads.DiscordReads(token="")
    reads.client = client
    return reads


@pytest.fixture
def rest_reads() -> discord_reads.DiscordReads:
    """Reads over a stand-in client that knows one guild, and one of its forums."""
    return _reads(api_client(
        _reading_guild(),
        forums=[ApiChannel(12, "announce-forum", discord.ChannelType.forum,
                           [ApiTag(30, "Game"), ApiTag(31, "LFG")])]))


class TestChoices:
    """What the panel offers as picker choices, and with which labels."""

    async def test_the_text_and_news_channels_are_offered(self, rest_reads):
        assert await rest_reads.channels(7) == [
            {"id": 10, "label": "#general"},
            {"id": 11, "label": "#news"}]

    async def test_forums_are_offered_on_their_own(self, rest_reads):
        assert await rest_reads.channels(
            7, discord_reads.FORUM_CHANNEL_TYPES) == [
            {"id": 12, "label": "#announce-forum"}]

    async def test_everyone_and_managed_roles_are_left_out(self, rest_reads):
        assert await rest_reads.roles(7) == [{"id": 20, "label": "@Raid"}]

    async def test_a_forums_tags_are_offered(self, rest_reads):
        assert await rest_reads.forum_tags(12) == [
            {"id": 30, "label": "Game"}, {"id": 31, "label": "LFG"}]

    async def test_the_bots_servers_are_offered_with_their_names(self, rest_reads):
        assert await rest_reads.guilds() == [{"id": 7, "name": "Server Seven"}]

    async def test_a_server_is_named_from_that_list(self, rest_reads):
        assert await rest_reads.name_of(7) == "Server Seven"

    async def test_a_server_the_list_does_not_hold_has_no_name(self, rest_reads):
        assert await rest_reads.name_of(8) is None

    async def test_stored_mentions_are_shown_with_their_names(self, rest_reads):
        labels = await rest_reads.labels(7)
        assert labels["<#10>"] == "#general"
        assert labels["<@&20>"] == "@Raid"


class TestCache:
    """A page render must not ask Discord the same thing twice."""

    async def test_a_guild_is_read_once_for_its_channels_and_roles(self, rest_reads):
        await rest_reads.channels(7)
        await rest_reads.roles(7)
        assert rest_reads.client.guild_fetches == 1

    async def test_the_server_list_is_read_once(self, rest_reads):
        await rest_reads.guilds()
        rest_reads.client.guilds = []
        assert await rest_reads.guilds() == [{"id": 7, "name": "Server Seven"}]

    async def test_another_instance_starts_from_nothing(self, rest_reads):
        # The cache belongs to the instance: no process-wide state to reset.
        await rest_reads.channels(7)
        other = _reads(api_client(_reading_guild()))
        await other.channels(7)
        assert other.client.guild_fetches == 1


class TestWithoutAClient:
    """What the pages read when Discord cannot be reached at all."""

    async def test_every_read_is_empty(self):
        reads = _reads()
        assert await reads.guilds() == []
        assert await reads.channels(7) == []
        assert await reads.roles(7) == []
        assert await reads.forum_tags(12) == []
        assert await reads.labels(7) == {}

    async def test_nothing_can_be_read_and_nothing_is_named(self):
        reads = _reads()
        assert reads.can_read() is False
        assert await reads.name_of(7) is None


class TestOpenAndClose:
    """Opening is a REST login with the bot token, never a gateway connection."""

    async def test_a_missing_token_leaves_it_unopened(self, capsys):
        reads = _reads()
        await reads.open()
        assert reads.client is None
        assert "DISCORD_TOKEN is not set" in capsys.readouterr().out

    async def test_a_refused_token_is_reported_and_closed(self, monkeypatch,
                                                          capsys):
        async def refuse(self, token):
            raise discord.LoginFailure("Improper token")

        monkeypatch.setattr(discord.Client, "login", refuse)
        reads = discord_reads.DiscordReads(token="refused")
        await reads.open()
        assert reads.client is None
        assert "Improper token" in capsys.readouterr().out

    async def test_an_accepted_token_keeps_the_client(self, monkeypatch):
        async def accept(self, token):
            return None

        monkeypatch.setattr(discord.Client, "login", accept)
        reads = discord_reads.DiscordReads(token="accepted")
        await reads.open()
        assert isinstance(reads.client, discord.Client)
        await reads.close()
        assert reads.client is None

    async def test_closing_without_a_client_is_harmless(self):
        reads = _reads()
        await reads.close()
        assert reads.client is None

    async def test_a_logged_in_client_means_discord_can_be_read(self, rest_reads):
        assert rest_reads.can_read() is True


class TestMentions:
    """The mention forms the configuration stores, and reads back."""

    def test_a_channel_and_a_role_mention(self):
        assert discord_reads.channel_mention(10) == "<#10>"
        assert discord_reads.role_mention(20) == "<@&20>"

    def test_a_user_mention(self):
        assert discord_reads.user_mention(42) == "<@42>"

    def test_the_id_of_a_mention(self):
        assert discord_reads.mention_id("<#10>") == 10
        assert discord_reads.mention_id("<@&20>") == 20
        assert discord_reads.mention_id("<@42>") == 42
        assert discord_reads.mention_id("not a mention") is None
        assert discord_reads.mention_id(None) is None


class TestMemberReads:
    """The member reads a ping needs: a search, a single fetch, never a list."""

    def _reads_with(self, *members) -> discord_reads.DiscordReads:
        return _reads(api_client(
            api_guild(7, "Server Seven", members=list(members))))

    async def test_a_searched_name_comes_back_as_a_mention(self):
        reads = self._reads_with(ApiMember(42, "hosty", nick="Hosty"),
                                 ApiMember(43, "guest"))
        assert await reads.member_search(7, "host") == [
            {"id": "<@42>", "label": "@Hosty"}]

    async def test_a_name_nobody_matches_comes_back_empty(self):
        assert await self._reads_with(ApiMember(42, "hosty")).member_search(
            7, "nobody") == []

    async def test_a_member_is_named_by_id(self):
        reads = self._reads_with(ApiMember(42, "hosty", global_name="Hosty"))
        assert await reads.member_name(7, 42) == "Hosty"

    async def test_a_member_discord_does_not_know_is_not_named(self):
        reads = self._reads_with()
        assert await reads.member_name(7, 42) is None

    async def test_a_member_is_asked_for_once(self):
        reads = self._reads_with(ApiMember(42, "hosty"))
        await reads.member_name(7, 42)
        await reads.member_name(7, 42)
        assert reads.client.guilds[0].member_fetches == 1

    async def test_a_missing_member_is_not_asked_for_again(self):
        reads = self._reads_with()
        await reads.member_name(7, 42)
        await reads.member_name(7, 42)
        assert reads.client.guilds[0].member_fetches == 1

    async def test_a_user_mention_is_shown_as_its_members_name(self):
        reads = self._reads_with(ApiMember(42, "hosty", nick="Hosty"))
        assert await reads.mention_label(7, "<@42>") == "@Hosty"

    async def test_a_role_mention_is_shown_from_the_roles(self):
        reads = _reads(api_client(_reading_guild()))
        assert await reads.mention_label(7, "<@&20>") == "@Raid"

    async def test_a_mention_discord_does_not_know_is_shown_as_it_is(self):
        reads = self._reads_with()
        assert await reads.mention_label(7, "<@999>") == "<@999>"
        assert await reads.mention_label(7, "") == ""
        assert await reads.mention_label(7, "not a mention") == "not a mention"

    async def test_without_a_client_nothing_is_read(self):
        reads = _reads()
        assert await reads.member_search(7, "hosty") == []
        assert await reads.member_name(7, 42) is None
        assert await reads.mention_label(7, "<@42>") == "<@42>"
