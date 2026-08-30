"""Database layer for the bot's database-backed configuration.

``db.db`` owns the Tortoise connection lifecycle and the schema creation;
``db.models`` holds the Tortoise models. The cog-specific mapping between
the config files and the rows lives with the cog that owns the config format
(``cogs.matchmaking.db_config``), keeping this package cog-agnostic.
"""

from .db import Database

__all__ = ["Database"]
