"""Tests for the configuration change log (db/config_log.py and its writers).

Database tests run against TEST_DATABASE_URL, like the other db suites: they
skip when it is unset or the server is unreachable (see the ``db`` fixture).
"""

from types import SimpleNamespace

from db import config_log, models

from cogs.matchmaking import db_config
from cogs.matchmaking.cog import Matchmaking
from cogs.matchrolls import db_config as rolls_db_config
from cogs.matchrolls.cog import MatchRolls

from tests.conftest import FakeBot, FakeInteraction, FakeMember

# The guilds the config fixtures define (tests/fixtures/).
GAMES_GUILD_ID = 90401
ROLLS_GUILD_ID = 42424


def _manager(user_id=42) -> FakeMember:
    member = FakeMember(user_id, "Manager")
    member.guild_permissions = SimpleNamespace(manage_guild=True)
    return member


def _db_bot() -> FakeBot:
    """A fake bot that reports running in database mode."""
    bot = FakeBot()
    bot.db = SimpleNamespace(fresh=False)
    return bot


class TestRecordChange:
    """The log itself: who wrote, where, and whether the bot still owes it."""

    async def test_a_web_change_is_pending(self, db):
        change = await config_log.record_change(
            GAMES_GUILD_ID, actor_id=7, actor_name="Webbie",
            source=config_log.SOURCE_WEB, action="game.add", summary="root")
        assert change.applied_at is None
        stored = await models.ConfigChange.get(id=change.id)
        assert (stored.guild_id, stored.actor_id, stored.actor_name,
                stored.source, stored.action, stored.summary) == (
            GAMES_GUILD_ID, 7, "Webbie", "web", "game.add", "root")

    async def test_a_command_change_is_already_applied(self, db):
        interaction = FakeInteraction(user=_manager(), guild_id=GAMES_GUILD_ID)
        change = await config_log.record_command_change(
            interaction, "game.add", "root")
        assert change.source == config_log.SOURCE_DISCORD
        assert (change.actor_id, change.actor_name) == (42, "Manager")
        assert change.applied_at is not None

    async def test_pending_changes_are_grouped_by_guild(self, db):
        for guild_id, action in ((1, "game.add"), (2, "game.add"),
                                 (2, "game.update")):
            await config_log.record_change(
                guild_id, actor_id=7, actor_name="Webbie",
                source=config_log.SOURCE_WEB, action=action)
        interaction = FakeInteraction(user=_manager(), guild_id=3)
        await config_log.record_command_change(interaction, "game.add")
        pending = await config_log.pending_changes()
        # The Discord change was applied by the command itself, so it is not owed.
        assert sorted(pending) == [1, 2]
        assert [change.action for change in pending[2]] == [
            "game.add", "game.update"]

    async def test_mark_applied_stamps_a_batch(self, db):
        await config_log.record_change(
            1, actor_id=7, actor_name="Webbie",
            source=config_log.SOURCE_WEB, action="game.add")
        pending = await config_log.pending_changes()
        await config_log.mark_applied(pending[1])
        assert await config_log.pending_changes() == {}
        stored = await models.ConfigChange.get(guild_id=1)
        assert stored.applied_at is not None


class TestCommandWritesAreLogged:
    """Every /games and /rollsets write leaves a row saying what it changed."""

    async def _cog(self, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config,
                                            game_parameters_config)
        return Matchmaking(bot=_db_bot(),
                           loaded_config=await db_config.load_config_from_db())

    async def test_games_add_is_logged(self, db, games_config,
                                       game_parameters_config):
        cog = await self._cog(games_config, game_parameters_config)
        interaction = FakeInteraction(user=_manager(), guild_id=GAMES_GUILD_ID)
        await Matchmaking.games_add.callback(
            cog, interaction, command="newgame", name="New Game")
        change = await models.ConfigChange.get()
        assert (change.guild_id, change.source, change.action,
                change.summary) == (
            GAMES_GUILD_ID, "discord", "game.add", "newgame")
        assert (change.actor_id, change.actor_name) == (42, "Manager")
        assert change.applied_at is not None
        # The row the log points at is there too.
        assert await models.Game.get_or_none(
            guild_id=GAMES_GUILD_ID, command="newgame") is not None

    async def test_a_refused_game_add_is_not_logged(self, db, games_config,
                                                    game_parameters_config):
        cog = await self._cog(games_config, game_parameters_config)
        interaction = FakeInteraction(user=_manager(), guild_id=GAMES_GUILD_ID)
        await Matchmaking.games_add.callback(
            cog, interaction, command="game_c", name="Again")
        assert "already configured" in interaction.response.messages[0][0]
        assert await models.ConfigChange.all().count() == 0

    async def test_a_game_rename_is_logged(self, db, games_config,
                                          game_parameters_config):
        cog = await self._cog(games_config, game_parameters_config)
        interaction = FakeInteraction(user=_manager(), guild_id=GAMES_GUILD_ID)
        await Matchmaking.games_update.callback(
            cog, interaction, command="game_c", new_command="game_renamed")
        change = await models.ConfigChange.get()
        assert (change.action, change.summary) == (
            "game.rename", "game_c -> game_renamed")

    async def test_a_parameter_add_is_logged(self, db, games_config,
                                             game_parameters_config):
        cog = await self._cog(games_config, game_parameters_config)
        interaction = FakeInteraction(user=_manager(), guild_id=GAMES_GUILD_ID)
        await Matchmaking.games_parameter_add.callback(
            cog, interaction, game="game_c", name="newparam", values="a, b")
        change = await models.ConfigChange.get()
        assert (change.action, change.summary) == (
            "parameter.add", "game_c/newparam")

    async def test_rollset_add_is_logged(self, db, rolls_config, descriptions):
        await rolls_db_config.seed_db_from_config(rolls_config, descriptions)
        cog = MatchRolls(
            bot=_db_bot(),
            loaded_config=await rolls_db_config.load_config_from_db())
        interaction = FakeInteraction(user=_manager(), guild_id=ROLLS_GUILD_ID)
        await MatchRolls.rollsets_add.callback(
            cog, interaction, category="weapons", items="Sword, Axe")
        change = await models.ConfigChange.get()
        assert (change.guild_id, change.action, change.summary) == (
            ROLLS_GUILD_ID, "rollset.category.add", "weapons")
        assert await models.RollCategory.get_or_none(
            guild__guild_id=ROLLS_GUILD_ID, name="weapons") is not None

    async def test_a_description_variant_is_logged(self, db, rolls_config,
                                                   descriptions):
        await rolls_db_config.seed_db_from_config(rolls_config, descriptions)
        cog = MatchRolls(
            bot=_db_bot(),
            loaded_config=await rolls_db_config.load_config_from_db())
        interaction = FakeInteraction(user=_manager(), guild_id=ROLLS_GUILD_ID)
        await MatchRolls.description_add.callback(
            cog, interaction, item="Zeta", text="A flavour text.")
        change = await models.ConfigChange.get()
        assert (change.action, change.summary) == (
            "rollset.description.add", "Zeta")
