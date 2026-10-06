"""Tests for the bot's database lifecycle (db/db.py).

Database tests run against TEST_DATABASE_URL, like the other db suites: they
skip when it is unset or the server is unreachable (see the ``db`` fixture).
"""

import tortoise.context as tortoise_context

from db import models


class TestInitialize:
    """Opening the bot's connection to the configuration store."""

    async def test_an_empty_database_is_fresh(self, db):
        assert db.fresh is True

    async def test_a_query_from_a_context_that_did_not_init_works(self, db):
        # A command handler's or the watcher's task does not inherit the context
        # Database.initialize() opened; clearing the context var stands in for
        # that task, leaving the global fallback as the only way to the
        # connection.
        token = tortoise_context._current_context.set(None)
        try:
            assert await models.Guild.all().count() == 0
        finally:
            tortoise_context._current_context.reset(token)
