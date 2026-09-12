"""Configuration loading for the matchrolls cog: rolls.ini parsing and lookup."""

import configparser

from common import constants as common_constants


def _get_guild_id(config: configparser.ConfigParser, section: str) -> int | None:
    """The section's ``ID`` key, or None when absent or not an integer."""
    try:
        return config.getint(section, common_constants.CONFIG_ID, fallback=None)
    except ValueError:
        return None


class RollsConfigMixin:
    """Roll-set parsing and lookup."""

    # category -> verbatim comma-separated set ([DEFAULT] section)
    default_categories: dict[str, str]
    # guild_id -> {category: verbatim set}
    guilds: dict[int, dict[str, str]]
    # The [DEFAULT] guild's description embeds, inherited by every guild.
    default_descriptions: list[dict]
    # guild_id -> its own description embeds
    guild_descriptions: dict[int, list[dict]]

    @staticmethod
    def _load_config(config: configparser.ConfigParser,
                     ) -> tuple[dict[str, str], dict[int, dict[str, str]]]:
        """Parse rolls.ini into (default categories, per-guild categories).

        Guild sections inherit the [DEFAULT] categories (configparser
        semantics); their ``ID`` key identifies the guild and is not a
        category. Sections without an ID, or with no category at all,
        are skipped.
        """
        default_categories = dict(config.defaults())
        guilds: dict[int, dict[str, str]] = {}
        for section in config.sections():
            guild_id = _get_guild_id(config, section)
            if (guild_id is None):
                continue
            # optionxform lowercases keys, so the ID key appears as 'id'.
            roll_sets = {
                name: value for name, value in config.items(section)
                if (name != common_constants.CONFIG_ID.lower())
            }
            if (roll_sets):
                guilds[guild_id] = roll_sets
        return default_categories, guilds

    def get_roll_sets(self, guild_id: int | None) -> dict[str, str]:
        """A guild's ``{category: set}`` map; falls back to [DEFAULT]."""
        if (guild_id is None):
            return self.default_categories
        return self.guilds.get(guild_id, self.default_categories)

    def get_descriptions(self, guild_id: int | None) -> list[dict]:
        """A guild's description embeds: its own when materialized, else the
        [DEFAULT] ones."""
        if (guild_id is None):
            return list(self.default_descriptions)
        if (guild_id in self.guilds):
            return list(self.guild_descriptions.get(guild_id, []))
        return list(self.default_descriptions)
