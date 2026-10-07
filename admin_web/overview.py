"""What the pages show about a server, read through the cogs' own loaders."""

from cogs.matchmaking import constants as mm_constants
from cogs.matchmaking import db_config as games_db_config
from cogs.matchmaking import validation
from cogs.matchrolls import db_config as rolls_db_config
from db import config_log, models

from . import discord_reads, forms


async def guild_config(reads: discord_reads.DiscordReads, guild_id: int) -> dict:
    """A server's games and roll categories, as the pages list them.

    A server without configuration of its own falls back to the ``[DEFAULT]``
    one, exactly as the cogs do; ``inherited`` says that it does.
    """
    loaded = await games_db_config.load_config_from_db()
    guild_games = loaded.guilds.get(guild_id, loaded.default_guild_config)
    labels = await _labels(reads, guild_id)
    # Only the rows the server owns are editable here: an inherited game is
    # edited on the [DEFAULT] page, where the row lives.
    row_ids = {game.command: game.id
               for game in await models.Game.filter(guild_id=guild_id)}
    # A ping is a role or a user mention; its label is resolved once per ping.
    pings = {option.role for option in guild_games.games.values() if option.role}
    ping_labels = {mention: await reads.mention_label(guild_id, mention)
                   for mention in pings}
    games = [_game(command, option, _parameters(loaded, guild_id, command),
                   labels, ping_labels.get(option.role, option.role),
                   row_ids.get(command))
             for command, option in sorted(
                 guild_games.games.items(),
                 key=lambda item: (item[1].name or item[0]).lower())]
    categories = await rolls_db_config.effective_category_sets(guild_id)
    rolls = [{"name": name,
              "names": [item.strip() for item in (roll_set or "").split(",")
                        if item.strip()]}
             for name, roll_set in categories.items()]
    return {"games": games, "rolls": rolls,
            "missing": _missing_games(loaded, guild_id, guild_games),
            "inherited": guild_id not in loaded.guilds,
            "changes": await config_log.changes_for_guild(guild_id)}


def _missing_games(loaded, guild_id: int, guild_games) -> list[str]:
    """The [DEFAULT] games a server with its own configuration does not have.

    A server that owns rows inherits nothing, so a game [DEFAULT] gained after
    its copy was made is offered as one to bring in. Empty for a server that
    inherits everything: it already shows them.
    """
    if (guild_id not in loaded.guilds):
        return []
    return sorted(set(loaded.default_guild_config.games)
                  - set(guild_games.games))


def _game(command: str, option, parameters: dict, labels: dict[str, str],
          ping: str, game_id: int | None) -> dict:
    """One game as a page shows it: what it pings, where it posts, its options.

    ``game_id`` is the row the server owns, or None when the game is inherited
    from [DEFAULT]: the page only offers the actions it can carry out.
    """
    return {
        "id": game_id,
        "command": command,
        "name": option.name,
        "players": (option.default_max_guests + 1
                    if option.default_max_guests is not None else None),
        "ping": ping,
        "channel": labels.get(option.channel, option.channel),
        "forum": labels.get(option.forum, option.forum),
        "token": bool(option.api_token),
        "parameters": sorted(parameters),
    }


async def _labels(reads: discord_reads.DiscordReads,
                  guild_id: int) -> dict[str, str]:
    """The server's channel/role mentions mapped to names, when Discord answers."""
    try:
        return await reads.labels(guild_id)
    except discord_reads.UNREACHABLE:
        return {}


def _parameters(loaded, guild_id: int, command: str) -> dict:
    """A game's parameters, as the cog resolves them: the guild's, else [DEFAULT]."""
    by_guild = loaded.game_parameters.get(
        guild_id, loaded.game_parameters.get(mm_constants.DEFAULT_GUILD_ID, {}))
    return by_guild.get(command, {})


def _api_fields(loaded, guild_id: int, command: str) -> dict[str, str]:
    """A game's api_* field names, as the cog resolves them."""
    by_guild = loaded.game_api_fields.get(
        guild_id, loaded.game_api_fields.get(mm_constants.DEFAULT_GUILD_ID, {}))
    return by_guild.get(command, {})


async def game_detail(reads: discord_reads.DiscordReads, guild_id: int,
                      game_id: int) -> dict | None:
    """One game's stored values, its parameters and the choices a form offers.

    None when the server has no game row with that id, which the view answers
    as not found.
    """
    row = await models.Game.get_or_none(id=game_id, guild_id=guild_id)
    if (row is None):
        return None
    loaded = await games_db_config.load_config_from_db()
    guild_games = loaded.guilds.get(guild_id, loaded.default_guild_config)
    option = guild_games.games.get(row.command)
    if (option is None):
        return None
    return await _game_context(reads, guild_id, loaded, guild_games, option,
                               row, game_id)


async def blank_game(reads: discord_reads.DiscordReads, guild_id: int) -> dict:
    """The empty values and the choices a new game's form starts from."""
    loaded = await games_db_config.load_config_from_db()
    return {
        "game": None,
        "values": _form_values(None),
        "color_hex": forms.NO_COLOR,
        "color_set": False,
        "parameters": [],
        "api_values": {argument: "" for argument in validation.API_FIELD_ARGUMENTS},
        "api_defaults": _api_defaults(loaded),
        "choices": await _choices(reads, guild_id, {}),
    }


async def ping_detail(reads: discord_reads.DiscordReads, guild_id: int,
                      game_id: int) -> dict | None:
    """One game's ping page: the roles to pick from, and the ping it has now.

    None when the server has no game row with that id, which the view answers
    as not found.
    """
    row = await models.Game.get_or_none(id=game_id, guild_id=guild_id)
    if (row is None):
        return None
    loaded = await games_db_config.load_config_from_db()
    guild_games = loaded.guilds.get(guild_id, loaded.default_guild_config)
    option = guild_games.games.get(row.command)
    if (option is None):
        return None
    return {
        "game_id": game_id,
        "game": option,
        "roles": await _role_choices(reads, guild_id),
        "ping": option.role,
        "ping_label": await reads.mention_label(guild_id, option.role),
    }


async def _game_context(reads: discord_reads.DiscordReads, guild_id: int,
                        loaded, guild_games, option, row, game_id: int) -> dict:
    """One game's form context: its values, parameters, overrides and choices."""
    values = _form_values(option)
    api_fields = _api_fields(loaded, guild_id, row.command)
    return {
        "game_id": game_id,
        "game": option,
        "values": values,
        "commands": sorted(guild_games.games),
        "color_hex": forms.color_hex(option.color),
        "color_set": forms.color_set(option.color),
        "parameters": await _parameter_rows(loaded, guild_id, row, api_fields),
        "api_values": {argument: api_fields.get(key, "")
                       for argument, key in validation.API_FIELD_ARGUMENTS.items()},
        "api_defaults": _api_defaults(loaded),
        "choices": await _choices(reads, guild_id, values),
    }


def _api_defaults(loaded) -> dict[str, str]:
    """The inherited match API field names, by the form field that overrides them."""
    return {argument: loaded.default_api_fields.get(key, "")
            for argument, key in validation.API_FIELD_ARGUMENTS.items()}


# The values a game form reads from its values; a new game's form carries the
# same keys, empty, so a template never reads a name that was not given.
GAME_FORM_KEYS = ("command", "name", "channel", "forum", "tag", "visibility",
                  "icon", "message", "registration_api", "match_api",
                  "match_url", "website_url", "registration_url",
                  "profile_url", "max_players")


def _form_values(option) -> dict:
    """A game's stored values as the strings its form shows.

    ``option`` is None for a new game: the same keys are then given empty. The
    ping and the API token are not here: each is edited on its own page.
    """
    if (option is None):
        return dict.fromkeys(GAME_FORM_KEYS, "")
    return {
        "command": option.command,
        "name": option.name,
        "channel": option.channel,
        "forum": option.forum,
        "tag": option.tag or "",
        "visibility": option.visibility or "",
        "icon": option.icon,
        "message": option.message or "",
        "registration_api": option.registration_api or "",
        "match_api": option.match_api or "",
        "match_url": option.match_url or "",
        "website_url": option.website_url or "",
        "registration_url": option.registration_url or "",
        "profile_url": option.profile_url or "",
        "max_players": (str(option.default_max_guests + 1)
                        if option.default_max_guests is not None else ""),
    }


async def _parameter_rows(loaded, guild_id: int, row, api_fields: dict[str, str],
                          ) -> list[dict]:
    """A game's parameters as the rows its form lists, keyed by their row id."""
    parameters = _parameters(loaded, guild_id, row.command)
    row_ids = {parameter.name: parameter.id
               for parameter in await models.GameParameter.filter(game_id=row.id)}
    return [{"id": row_ids.get(name),
             "name": name,
             "display_name": definition.get("display_name") or name,
             "values_text": forms.parameter_values_text(
                 definition.get("values", {})),
             "api_field": api_fields.get(name, "")}
            for name, definition in sorted(parameters.items())]


async def _choices(reads: discord_reads.DiscordReads, guild_id: int,
                   stored: dict) -> dict:
    """The Discord choices a game's form offers, as the values storage keeps.

    Without a readable Discord every list is empty: the form then shows plain
    text inputs with the stored values, so an outage does not block editing.
    """
    if (not reads.can_read()):
        return {name: [] for name in ("channel", "forum", "tag")}
    try:
        channels = [_mention(choice, discord_reads.channel_mention)
                    for choice in await reads.channels(guild_id)]
        forums = await reads.channels(guild_id, discord_reads.FORUM_CHANNEL_TYPES)
        tags = await _tag_choices(reads, forums)
    except discord_reads.UNREACHABLE:
        return {name: [] for name in ("channel", "forum", "tag")}
    return {
        "channel": _picker(channels, stored.get("channel", "")),
        "forum": _picker([_mention(choice, discord_reads.channel_mention)
                          for choice in forums], stored.get("forum", "")),
        "tag": _picker(tags, stored.get("tag", "")),
    }


async def _tag_choices(reads: discord_reads.DiscordReads, forums: list[dict],
                       ) -> list[dict]:
    """Every forum's tags, by the name the configuration stores and its forum."""
    tags = []
    seen: set[str] = set()
    for forum in forums:
        for tag in await reads.forum_tags(forum["id"]):
            # The cog matches the stored value against the forum's tag names,
            # so the name is what a picker picks.
            if (tag["label"] in seen):
                continue
            seen.add(tag["label"])
            tags.append({"value": tag["label"],
                         "label": f"{forum['label']} — {tag['label']}"})
    return tags


async def _role_choices(reads: discord_reads.DiscordReads,
                        guild_id: int) -> list[dict]:
    """The guild's roles as picker choices (Discord caps how many one has)."""
    try:
        roles = await reads.roles(guild_id)
    except discord_reads.UNREACHABLE:
        return []
    return [_mention(choice, discord_reads.role_mention) for choice in roles]


def _mention(choice: dict, as_mention) -> dict:
    """A read choice whose id is in the mention form the configuration stores."""
    return {"value": as_mention(choice["id"]), "label": choice["label"]}


def _picker(choices: list[dict], stored: str) -> list[dict]:
    """The choices, plus the stored value when Discord no longer offers it."""
    if (stored and all(choice["value"] != stored for choice in choices)):
        return choices + [{"value": stored,
                           "label": f"{stored} (no longer here)"}]
    return choices
