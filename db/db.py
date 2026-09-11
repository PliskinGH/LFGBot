"""Async database access for the bot's configuration store.

``bot.py`` creates a ``Database`` only when ``DATABASE_URL`` is set; any
initialization failure degrades to config-file mode (Tortoise ORM).

The database itself is provisioned by the host (e.g. Dokku's
``postgres:create``/``postgres:link``), and the schema is managed by the
committed migrations in ``db/migrations``, applied at deploy time with
``tortoise -c db.orm_config.TORTOISE_ORM migrate``.
"""

from tortoise import Tortoise

from . import models
from .orm_config import orm_config


class Database:
    """Owns the Tortoise connection lifecycle."""

    def __init__(self, url: str):
        self._url = url
        # Set by initialize(): True when the database held no guild rows.
        self.fresh: bool = False

    async def initialize(self) -> bool:
        """Connect and report whether the database held no guild rows."""
        await Tortoise.init(config=orm_config(self._url))
        self.fresh = (await models.Guild.all().count() == 0)
        return self.fresh

    async def close(self) -> None:
        """Close all Tortoise connections."""
        await Tortoise.close_connections()

