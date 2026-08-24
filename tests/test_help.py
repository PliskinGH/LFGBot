"""Tests for the ``cogs/help.py`` cog."""
import pytest

from cogs.help import Help
from common.common import HELP_COMMAND
from cogs.matchmaking import Matchmaking

from tests.conftest import (
    FakeBot,
    FakeCommand,
    FakeInteraction,
    FakeMember,
    FakeTree,
)


@pytest.fixture
def bot_with_commands(games_config):
    """A FakeBot whose 'Matchmaking' cog is registered (as the real bot does)."""
    bot = FakeBot()
    bot.add_cog(Matchmaking(bot=bot, config=games_config))
    return bot


class FakeTreeWithCommands(FakeTree):
    def get_command(self, name):
        for command in self._commands:
            if command.name == name:
                return command
        return None


class TestTopicAutocomplete:
    @pytest.mark.asyncio
    async def test_excludes_help_and_filters(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands(
            [FakeCommand("lfg"), FakeCommand("random"), FakeCommand(HELP_COMMAND)]
        )
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await help_cog.topic_autocomplete(interaction, "ra")
        assert [choice.value for choice in choices] == ["random"]

    @pytest.mark.asyncio
    async def test_returns_all_topics_sorted(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands(
            [FakeCommand("lfg"), FakeCommand("random"), FakeCommand(HELP_COMMAND)]
        )
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await help_cog.topic_autocomplete(interaction, "")
        assert [choice.value for choice in choices] == ["lfg", "random"]


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_unknown_topic_reports_unavailable(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands(
            [FakeCommand("lfg"), FakeCommand("random"), FakeCommand(HELP_COMMAND)]
        )
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        # `help` is an @app_commands.command; call the underlying callback.
        await Help.help.callback(help_cog, interaction, "unknown")

        content = interaction.response.messages[0][0]
        assert content == "`/unknown` is not currently available."
        assert interaction.response.messages[0][2] is True  # ephemeral

    @pytest.mark.asyncio
    async def test_known_topic_dispatches_to_cog(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands(
            [FakeCommand("lfg"), FakeCommand("random"), FakeCommand(HELP_COMMAND)]
        )
        help_cog = Help(bot=bot_with_commands)
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        await Help.help.callback(help_cog, interaction, "lfg")

        # Matchmaking.send_help receives (interaction, topic) and sends its help text.
        content = interaction.response.messages[0][0]
        assert "# Help: /lfg" in content

    @pytest.mark.asyncio
    async def test_known_topic_but_cog_unavailable(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands(
            [FakeCommand("lfg"), FakeCommand("random"), FakeCommand(HELP_COMMAND)]
        )
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        await Help.help.callback(help_cog, interaction, "random")

        content = interaction.response.messages[0][0]
        assert "Help is currently unavailable." in content