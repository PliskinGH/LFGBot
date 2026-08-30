"""Async database access for the bot's configuration store.

``bot.py`` creates a ``Database`` only when ``DATABASE_URL`` is set; any
initialization failure degrades to config-file mode (Tortoise ORM).

The database itself is provisioned by the host (e.g. Dokku's
``postgres:create``/``postgres:link``); the bot only creates its tables on
first start.
"""

from tortoise import Tortoise

from . import models


class Database:
    """Owns the Tortoise connection lifecycle and the schema creation."""

    def __init__(self, url: str):
        self._url = url
        # Set by initialize(): True when the database should be seeded.
        self.fresh: bool = False

    async def initialize(self) -> bool:
        """Create missing tables and report whether the database is empty
        (a first initialization that should be seeded from the config files)."""
        await Tortoise.init(db_url=self._url, modules={"models": ["db.models"]})
        await Tortoise.generate_schemas(safe=True)
        self.fresh = (await models.Guild.all().count() == 0)
        return self.fresh

    async def close(self) -> None:
        """Close all Tortoise connections."""
        await Tortoise.close_connections()

