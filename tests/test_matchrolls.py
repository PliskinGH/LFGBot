"""Tests for the ``cogs/matchrolls.py`` cog."""
import pytest

from cogs import matchrolls as matchrolls_mod
from cogs.matchrolls import RANDOM_COMMAND, MatchRolls

from tests.conftest import FakeInteraction, FakeMember


class TestCategoryAutocomplete:
    @pytest.mark.asyncio
    async def test_default_guild_uses_default_section(self, matchrolls):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await matchrolls.category_autocomplete(interaction, "")
        assert [choice.value for choice in choices] == ["map", "landmark"]

    @pytest.mark.asyncio
    async def test_filters_by_current(self, matchrolls):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await matchrolls.category_autocomplete(interaction, "la")
        assert [choice.value for choice in choices] == ["landmark"]

    @pytest.mark.asyncio
    async def test_uses_guild_specific_section(self, matchrolls):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=42424)
        choices = await matchrolls.category_autocomplete(interaction, "")
        # GuildB section exposes its own options (including the config 'id' key).
        assert "map" in [choice.value for choice in choices]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, matchrolls):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        assert await matchrolls.category_autocomplete(interaction, "zzz") == []


class TestSendHelp:
    def test_random_help_includes_usage_and_sets(self, matchrolls):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        await_test(matchrolls.send_help(interaction, RANDOM_COMMAND))

        content = interaction.response.messages[0][0]
        assert f"# Help: /{RANDOM_COMMAND}" in content
        assert f"/{RANDOM_COMMAND} category:<category>" in content
        assert "## Available sets" in content
        assert "map" in content
        assert "landmark" in content

    def test_unknown_topic_has_generic_help(self, matchrolls):
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        await_test(matchrolls.send_help(interaction, "other"))
        content = interaction.response.messages[0][0]
        assert "# Help: /other" in content
        assert "No detailed help is available" in content


class TestRandomCommand:
    def _interaction(self):
        return FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

    def _patch_random(self, monkeypatch):
        monkeypatch.setattr(matchrolls_mod.random, "choice",
                            lambda seq: seq[0])
        monkeypatch.setattr(matchrolls_mod.random, "randrange",
                            lambda *args, **kwargs: 0)

    async def _call_random(self, matchrolls, *args, **kwargs):
        # `@app_commands.command` replaces the method with a Command object;
        # the underlying coroutine is exposed on `.callback`.
        return await MatchRolls.random.callback(matchrolls, *args, **kwargs)

    @pytest.mark.asyncio
    async def test_unknown_category_reports_no_item(self, matchrolls, monkeypatch):
        interaction = self._interaction()
        self._patch_random(monkeypatch)

        await self._call_random(matchrolls, interaction, "not-a-set")

        content, embed, ephemeral, _ = interaction.response.messages[0]
        assert content == "No item found in the set or subset for `not-a-set`."
        assert embed is None
        assert ephemeral is True

    @pytest.mark.asyncio
    async def test_valid_category_sends_embed_publicly(self, matchrolls, monkeypatch):
        interaction = self._interaction()
        self._patch_random(monkeypatch)

        await self._call_random(matchrolls, interaction, "map")

        content, embed, ephemeral, _ = interaction.response.messages[0]
        assert content is None
        assert ephemeral is False  # public by default
        assert embed.title == "Random Map: Alpha"
        assert embed.author.name == "host"
        assert "Randomly chosen among: Alpha, Beta, Gamma." in embed.footer.text

    @pytest.mark.asyncio
    async def test_display_false_is_ephemeral(self, matchrolls, monkeypatch):
        interaction = self._interaction()
        self._patch_random(monkeypatch)

        await self._call_random(matchrolls, interaction, "map", display=False)

        content, embed, ephemeral, _ = interaction.response.messages[0]
        assert ephemeral is True

    @pytest.mark.asyncio
    async def test_subset_restricts_the_pool(self, matchrolls, monkeypatch):
        interaction = self._interaction()
        self._patch_random(monkeypatch)

        # subset "2" selects the FIRST TWO items of [Alpha, Beta, Gamma].
        await self._call_random(matchrolls, interaction, "map", subset="2")

        content, embed, ephemeral, _ = interaction.response.messages[0]
        assert embed.title == "Random Map: Alpha"
        assert "Randomly chosen among: Alpha, Beta." in embed.footer.text

    @pytest.mark.asyncio
    async def test_subset_out_of_range_reports_no_item(self, matchrolls, monkeypatch):
        interaction = self._interaction()
        self._patch_random(monkeypatch)

        # A range beyond the cardinality yields an empty pool -> "no item".
        await self._call_random(matchrolls, interaction, "map", subset="4-9")

        content, embed, ephemeral, _ = interaction.response.messages[0]
        assert content == "No item found in the set or subset for `map`."


def await_test(coro):
    import asyncio

    asyncio.run(coro)