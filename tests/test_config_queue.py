"""Tests for applying the web panel's queued writes (db/config_queue.py).

Database tests run against TEST_DATABASE_URL, like the other db suites: they
skip when it is unset or the server is unreachable (see the ``db`` fixture).
"""

import asyncio
from types import SimpleNamespace

from db import config_log, config_queue, models

from cogs.matchmaking import constants as mm_constants
from cogs.matchmaking import db_config
from cogs.matchmaking.cog import Matchmaking

from tests.conftest import FakeBot

# A guild the games config fixtures define (tests/fixtures/games.ini).
GAMES_GUILD_ID = 90401
ROLLS_GUILD_ID = 42424


class FakeGamesCog:
    """A stand-in with the seam the matchmaking admin mixin declares."""

    change_prefixes = ("game.", "parameter.")

    def __init__(self, sync_fails: bool = False):
        self.reloads = 0
        self.synced: list[set] = []
        self.sync_fails = sync_fails

    async def reload_config(self) -> None:
        self.reloads += 1

    async def sync_applied(self, guild_ids) -> None:
        self.synced.append(set(guild_ids))
        if (self.sync_fails):
            raise RuntimeError("the sync failed")


class FakeRollsCog:
    """A stand-in with the seam the matchrolls admin mixin declares."""

    change_prefixes = ("rollset.",)

    def __init__(self, fails: bool = False):
        self.reloads = 0
        self.fails = fails

    async def reload_config(self) -> None:
        self.reloads += 1
        if (self.fails):
            raise RuntimeError("the reload failed")


def _bot(*cogs) -> FakeBot:
    """A fake bot in database mode, with the given cogs loaded."""
    bot = FakeBot()
    bot.db = SimpleNamespace(fresh=False)
    for cog in cogs:
        bot.add_cog(cog)
    return bot


async def _queue(guild_id: int, action: str,
                 summary: str = "") -> models.ConfigChange:
    """Log a pending change, as a write from the web panel does."""
    return await config_log.record_change(
        guild_id, actor_id=7, actor_name="Webbie",
        source=config_log.SOURCE_WEB, action=action, summary=summary)


async def _stamped(change: models.ConfigChange) -> bool:
    """Whether the bot has marked a change applied."""
    return (await models.ConfigChange.get(id=change.id)).applied_at is not None


class TestApplyPending:
    """What a tick does with the changes the bot owes."""

    async def test_it_reloads_the_owning_cog_and_stamps_the_change(self, db):
        games, rolls = FakeGamesCog(), FakeRollsCog()
        change = await _queue(GAMES_GUILD_ID, "game.add", "root")

        assert await config_queue.apply_pending(_bot(games, rolls)) == 1
        assert (games.reloads, rolls.reloads) == (1, 0)
        assert games.synced == [{GAMES_GUILD_ID}]
        assert await _stamped(change) is True

    async def test_a_cog_reloads_once_for_several_changes(self, db):
        games = FakeGamesCog()
        for action in ("game.add", "parameter.add", "game.update"):
            await _queue(GAMES_GUILD_ID, action)

        assert await config_queue.apply_pending(_bot(games)) == 3
        assert games.reloads == 1
        assert games.synced == [{GAMES_GUILD_ID}]

    async def test_each_cog_gets_the_guilds_of_its_own_changes(self, db):
        games, rolls = FakeGamesCog(), FakeRollsCog()
        await _queue(GAMES_GUILD_ID, "game.add")
        await _queue(ROLLS_GUILD_ID, "rollset.category.add")

        assert await config_queue.apply_pending(_bot(games, rolls)) == 2
        assert (games.reloads, rolls.reloads) == (1, 1)
        assert games.synced == [{GAMES_GUILD_ID}]

    async def test_a_default_change_reaches_its_owner_as_guild_zero(self, db):
        games = FakeGamesCog()
        await _queue(mm_constants.DEFAULT_GUILD_ID, "game.update")

        assert await config_queue.apply_pending(_bot(games)) == 1
        assert games.synced == [{mm_constants.DEFAULT_GUILD_ID}]

    async def test_nothing_queued_touches_no_cog(self, db):
        games = FakeGamesCog()

        assert await config_queue.apply_pending(_bot(games)) == 0
        assert (games.reloads, games.synced) == (0, [])

    async def test_config_file_mode_owes_nothing(self, db):
        change = await _queue(GAMES_GUILD_ID, "game.add")
        bot = _bot(FakeGamesCog())
        bot.db = None

        assert await config_queue.apply_pending(bot) == 0
        assert await _stamped(change) is False


class TestFailures:
    """What stays owed when a tick goes wrong."""

    async def test_a_failed_reload_leaves_its_changes_queued(self, db, capsys):
        change = await _queue(ROLLS_GUILD_ID, "rollset.category.add")

        assert await config_queue.apply_pending(
            _bot(FakeRollsCog(fails=True))) == 0
        assert await _stamped(change) is False
        assert "its change(s) stay queued" in capsys.readouterr().out

    async def test_a_failed_cog_does_not_hold_back_the_others(self, db):
        games_change = await _queue(GAMES_GUILD_ID, "game.add")
        rolls_change = await _queue(ROLLS_GUILD_ID, "rollset.category.add")
        bot = _bot(FakeGamesCog(), FakeRollsCog(fails=True))

        assert await config_queue.apply_pending(bot) == 1
        assert await _stamped(games_change) is True
        assert await _stamped(rolls_change) is False

    async def test_a_change_no_cog_owns_stays_queued(self, db, capsys):
        change = await _queue(GAMES_GUILD_ID, "mystery.thing")

        assert await config_queue.apply_pending(_bot(FakeGamesCog())) == 0
        assert await _stamped(change) is False
        assert ("no loaded cog owns a 'mystery.thing' change"
                in capsys.readouterr().out)

    async def test_a_failed_command_sync_does_not_owe_the_change_again(
            self, db, capsys):
        change = await _queue(GAMES_GUILD_ID, "game.add")

        assert await config_queue.apply_pending(
            _bot(FakeGamesCog(sync_fails=True))) == 1
        # The configuration is already loaded by then, so the bot owes nothing.
        assert await _stamped(change) is True
        assert "could not re-sync the commands" in capsys.readouterr().out


class TestTheRealCogs:
    """The seam the cogs themselves declare."""

    async def test_a_queued_game_change_reaches_the_matchmaking_cog(
            self, db, games_config, game_parameters_config):
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        bot = _bot()
        cog = Matchmaking(bot=bot,
                          loaded_config=await db_config.load_config_from_db())
        bot.add_cog(cog)
        assert cog.change_prefixes == ("game.", "parameter.")

        await db_config.add_game(GAMES_GUILD_ID, "queued_game", name="Queued")
        assert "queued_game" not in cog.guilds[GAMES_GUILD_ID].games
        await _queue(GAMES_GUILD_ID, "game.add", "queued_game")

        assert await config_queue.apply_pending(bot) == 1
        assert "queued_game" in cog.guilds[GAMES_GUILD_ID].games
        assert bot.tree.sync_calls == [GAMES_GUILD_ID]

    async def test_a_default_change_resyncs_every_guild_the_bot_is_in(self, db):
        bot = _bot()
        bot.guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        cog = Matchmaking(bot=bot,
                          loaded_config=await db_config.load_config_from_db())
        bot.add_cog(cog)
        await _queue(mm_constants.DEFAULT_GUILD_ID, "game.update")

        assert await config_queue.apply_pending(bot) == 1
        assert bot.tree.sync_calls == [1, 2]

    async def test_two_reloads_of_one_cog_do_not_interleave(
            self, db, monkeypatch, games_config, game_parameters_config):
        # The slash commands and the watcher are two writers of the same
        # configuration: re-registering the commands twice at once would collide.
        await db_config.seed_db_from_config(games_config, game_parameters_config)
        bot = _bot()
        cog = Matchmaking(bot=bot,
                          loaded_config=await db_config.load_config_from_db())
        bot.add_cog(cog)
        order = []
        original = db_config.load_config_from_db

        async def tracked():
            order.append("start")
            await asyncio.sleep(0)          # let a second reload interleave
            order.append("end")
            return await original()

        monkeypatch.setattr(db_config, "load_config_from_db", tracked)
        await asyncio.gather(cog.reload_config(), cog.reload_config())
        assert order == ["start", "end", "start", "end"]
