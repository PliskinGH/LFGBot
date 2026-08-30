"""Tortoise ORM configuration, shared by the app and the migration CLI.

``orm_config`` builds the standard Tortoise config for a database URL; the
``TORTOISE_ORM`` variable is what the ``tortoise`` CLI reads (e.g.
``tortoise -c db.orm_config.TORTOISE_ORM makemigrations models``).
"""

import os

from dotenv import load_dotenv

load_dotenv()

MIGRATIONS_MODULE = "db.migrations"


def orm_config(db_url: str) -> dict:
    """Tortoise ORM configuration for the given database URL."""
    return {
        "connections": {"default": db_url},
        "apps": {
            "models": {
                "models": ["db.models"],
                "default_connection": "default",
                "migrations": MIGRATIONS_MODULE,
            },
        },
    }


TORTOISE_ORM = orm_config(os.getenv("DATABASE_URL") or "")