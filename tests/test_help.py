"""Tests for the ``cogs/help.py`` cog."""
import pytest

from cogs.help import Help
from common.common import HELP_COMMAND
from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.constants import LFG_COMMAND, RENAME_COMMAND

from tests.conftest import (
    FakeBot,
    FakeCommand,
    FakeInteraction,
    FakeMember,
    FakeTree,
)


@pytest.fixture
def bot_with_commands(games_config, game_parameters_config):
    """A FakeBot whose 'Matchmaking' cog is registered (as the real bot does)."""
    bot = FakeBot()
    bot.add_cog(Matchmaking(bot=bot, config=games_config,
                            game_parameters=game_parameters_config))
    return bot


class FakeTreeWithCommands(FakeTree):
    def get_command(self, name, *, guild=None, type=None):
        for command in self.get_commands(guild=guild):
            if command.name == name:
                return command
        return None


class TestTopicAutocomplete:
    @pytest.mark.asyncio
    async def test_excludes_help_and_filters(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands(
            [
                FakeCommand("lfg"),
                FakeCommand("random"),
                FakeCommand("rename"),
                FakeCommand(HELP_COMMAND),
            ]
        )
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await help_cog.topic_autocomplete(interaction, "ra")
        assert [choice.value for choice in choices] == ["random"]

    @pytest.mark.asyncio
    async def test_returns_all_topics_sorted(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands(
            [
                FakeCommand("lfg"),
                FakeCommand("random"),
                FakeCommand("rename"),
                FakeCommand(HELP_COMMAND),
            ]
        )
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await help_cog.topic_autocomplete(interaction, "")
        assert [choice.value for choice in choices] == ["lfg", "random", "rename"]

    @pytest.mark.asyncio
    async def test_includes_guild_scoped_commands(self, bot_with_commands):
        bot_with_commands.tree = FakeTreeWithCommands([FakeCommand("help")])
        bot_with_commands.tree._guild_commands = {1: [FakeCommand("game_a")]}
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        choices = await help_cog.topic_autocomplete(interaction, "")
        assert [choice.value for choice in choices] == ["game_a"]


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
    async def test_guild_scoped_command_dispatches_to_matchmaking(
        self, bot_with_commands
    ):
        bot_with_commands.tree = FakeTreeWithCommands([FakeCommand("help")])
        game_a = FakeCommand("game_a")
        # The cog that registers a dynamic command records itself on extras so
        # /help can find the owning cog without hardcoding names.
        game_a.extras = {"help_cog": bot_with_commands.get_cog("Matchmaking")}
        bot_with_commands.tree._guild_commands = {1: [game_a]}
        help_cog = Help(bot=bot_with_commands)

        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)
        await Help.help.callback(help_cog, interaction, "game_a")

        # game_a is parametrized: the alias help is content, the games list
        # and the parameters list are in embeds.
        content = interaction.response.messages[0][0]
        assert "# Help: /game_a" in content
        embeds = interaction.response.messages[0][1]
        assert [embed.title for embed in embeds] == ["Available games", "Game parameters"]

    @pytest.mark.parametrize(
        "topic,expected",
        [
            (LFG_COMMAND, f"# Help: /{LFG_COMMAND}"),
            (RENAME_COMMAND, f"# Help: /{RENAME_COMMAND}"),
        ],
    )
    @pytest.mark.asyncio
    async def test_known_topic_dispatches_to_cog(
        self, bot_with_commands, topic, expected
    ):
        bot_with_commands.tree = FakeTreeWithCommands(
            [
                FakeCommand("lfg"),
                FakeCommand("random"),
                FakeCommand("rename"),
                FakeCommand(HELP_COMMAND),
            ]
        )
        help_cog = Help(bot=bot_with_commands)
        interaction = FakeInteraction(user=FakeMember(1, "host"), guild_id=1)

        await Help.help.callback(help_cog, interaction, topic)

        # Matchmaking.send_help receives (interaction, topic) and sends its
        # help text as plain content (these topics have no parameters embed).
        content = interaction.response.messages[0][0]
        assert expected in content

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