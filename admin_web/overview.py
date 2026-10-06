"""What the pages show about a server, read through the cogs' own loaders."""

from cogs.matchmaking import constants as mm_constants
from cogs.matchmaking import db_config as games_db_config
from cogs.matchrolls import db_config as rolls_db_config
from db import config_log

from . import discord_reads


async def guild_config(reads: discord_reads.DiscordReads, guild_id: int) -> dict:
    """A server's games and roll categories, as the pages list them.

    A server without configuration of its own falls back to the ``[DEFAULT]``
    one, exactly as the cogs do; ``inherited`` says that it does.
    """
    loaded = await games_db_config.load_config_from_db()
    guild_games = loaded.guilds.get(guild_id, loaded.default_guild_config)
    parameters = (loaded.game_parameters.get(guild_id)
                  or loaded.game_parameters.get(mm_constants.DEFAULT_GUILD_ID, {}))
    labels = await _labels(reads, guild_id)
    games = [_game(command, option, parameters.get(command, {}), labels)
             for command, option in sorted(
                 guild_games.games.items(),
                 key=lambda item: (item[1].name or item[0]).lower())]
    categories = await rolls_db_config.effective_category_sets(guild_id)
    rolls = [{"name": name,
              "names": [item.strip() for item in (roll_set or "").split(",")
                        if item.strip()]}
             for name, roll_set in categories.items()]
    return {"games": games, "rolls": rolls,
            "inherited": guild_id not in loaded.guilds,
            "changes": await config_log.changes_for_guild(guild_id)}


def _game(command: str, option, parameters: dict, labels: dict[str, str]) -> dict:
    """One game as a page shows it: what it pings, where it posts, its options."""
    return {
        "command": command,
        "name": option.name,
        "players": (option.default_max_guests + 1
                    if option.default_max_guests is not None else None),
        "role": labels.get(option.role, option.role),
        "channel": labels.get(option.channel, option.channel),
        "forum": labels.get(option.forum, option.forum),
        "token": bool(option.api_token),
        "league": bool(option.match_api or option.registration_api),
        "parameters": sorted(parameters),
    }


async def _labels(reads: discord_reads.DiscordReads,
                  guild_id: int) -> dict[str, str]:
    """The server's channel/role mentions mapped to names, when Discord answers."""
    try:
        return await reads.labels(guild_id)
    except discord_reads.UNREACHABLE:
        return {}
