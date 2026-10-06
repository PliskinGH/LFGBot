"""Tests for the bot's configuration watcher (bot.py)."""

from types import SimpleNamespace

from bot import CONFIG_POLL_SECONDS, LFGBot


class TestConfigWatcher:
    """The loop that applies the writes the web panel queued."""

    def test_it_polls_on_the_configured_interval(self):
        assert LFGBot().config_watcher.seconds == CONFIG_POLL_SECONDS

    def test_it_does_not_run_without_a_database(self):
        # Config-file mode has no queue to watch.
        instance = LFGBot()
        instance._start_config_watcher()
        assert instance.config_watcher.is_running() is False

    async def test_it_runs_with_a_database_and_stops_on_close(self):
        instance = LFGBot()
        instance.db = SimpleNamespace(fresh=False)
        instance._start_config_watcher()
        assert instance.config_watcher.is_running() is True
        await instance.close()
        assert instance.config_watcher.is_running() is False
