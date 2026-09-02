"""INI <-> database mapping for the matchmaking cog's configuration.

With ``DATABASE_URL`` set, the database is the source of truth: seeded from
the config files on first initialization, loaded from it afterwards.
``LoadedConfig`` mirrors the structures ``ConfigMixin`` builds from the
files, so the cog consumes both sources identically; seeding preserves the
config files' ordering (slash-command option order, help, autocomplete).
"""

import configparser

from tortoise.transactions import in_transaction

from common import constants as common_constants
from db import models

from . import constants
from . import utils
from .config import ConfigMixin
from .models import GameOption, GuildGamesConfig, ParameterDefinition


class LoadedConfig:
    """The in-memory configuration structures ``Matchmaking`` consumes."""

    def __init__(self):
        self.guilds: dict[int, GuildGamesConfig] = {} # dict of guild_id -> GuildGamesConfig
        self.default_guild_config = GuildGamesConfig(None)
        # guild_id -> game_command -> { parameter_name -> {"display_name": label,
        # "values": {value: display_name}} }
        self.game_parameters: dict[int, dict[str, dict[str, ParameterDefinition]]] = {}
        # guild_id -> game_command -> { parameter_name or reserved api_* key -> match API field name }
        self.game_api_fields: dict[int, dict[str, dict[str, str]]] = {}
        # Fixed payload component field names from the [DEFAULT] section of
        # games_parameters.ini; inherited by every game (including games
        # without a section there), overridden by each game's own api_* keys.
        self.default_api_fields: dict[str, str] = {}


def _get_guild_id(config: configparser.ConfigParser, section: str) -> int | None:
    """The section's ``ID`` key, or None when absent or blank."""
    try:
        return config.getint(section, common_constants.CONFIG_ID, fallback=None)
    except ValueError:
        return None


def loaded_config_from_ini(config: configparser.ConfigParser,
                           game_parameters: configparser.ConfigParser) -> LoadedConfig:
    """Parse the games config files into a LoadedConfig (file-based mode)."""
    loaded = LoadedConfig()
    ConfigMixin._load_guild_config(loaded.default_guild_config, config, common_constants.CONFIG_DEFAULT)
    for guild in config.sections():
        guild_id = _get_guild_id(config, guild)
        if (guild_id is None):
            continue
        guild_config = GuildGamesConfig(guild_id)
        ConfigMixin._load_guild_config(guild_config, config, guild)
        loaded.guilds[guild_id] = guild_config
    (game_parameters, game_api_fields,
     default_api_fields) = utils.parse_game_parameters(game_parameters)
    # From the config files, every guild shares the same definitions; the
    # DEFAULT key holds them (guilds diverge only once edited via /games).
    loaded.game_parameters = {constants.DEFAULT_GUILD_ID: game_parameters}
    loaded.game_api_fields = {constants.DEFAULT_GUILD_ID: game_api_fields}
    loaded.default_api_fields = default_api_fields
    return loaded

# --------------------------------------------------------------------------- #
# Seeding (LoadedConfig -> rows)
# --------------------------------------------------------------------------- #

async def seed_db_from_config(config: configparser.ConfigParser,
                              game_parameters: configparser.ConfigParser) -> None:
    """Seed a fresh database from the games config files in one transaction.

    Only for a first initialization (empty database): rows are inserted
    unconditionally.
    """
    await seed_db(loaded_config_from_ini(config, game_parameters))


async def seed_db(loaded: LoadedConfig) -> None:
    """Persist a LoadedConfig as the database's whole configuration content."""
    async with in_transaction():
        for key, field_name in loaded.default_api_fields.items():
            await models.DefaultApiField.create(key=key, field_name=field_name)
        await _create_guild(constants.DEFAULT_GUILD_ID, loaded.default_guild_config, loaded)
        for guild_id, guild_config in loaded.guilds.items():
            await _create_guild(guild_id, guild_config, loaded)


async def _create_guild(guild_id: int, guild_config: GuildGamesConfig,
                        loaded: LoadedConfig) -> None:
    guild = await models.Guild.create(guild_id=guild_id)
    for command, option in guild_config.games.items():
        game = await models.Game.create(
            guild=guild,
            command=command,
            name=option.name,
            role=option.role,
            icon=option.icon,
            color=option.color,
            forum=option.forum,
            tag=option.tag,
            visibility=option.visibility,
            message=option.message,
            registration_api=option.registration_api,
            match_api=option.match_api,
            match_url=option.match_url,
            api_token_env_var=option.api_token_env_var,
            website_url=option.website_url,
            registration_url=option.registration_url,
            profile_url=option.profile_url,
            default_max_guests=option.default_max_guests,
        )
        # The guild's parameters/overrides: from its own map, falling back to
        # the DEFAULT definitions (the config files are shared by every guild).
        api_fields = (loaded.game_api_fields
                      .get(guild_id,
                           loaded.game_api_fields.get(constants.DEFAULT_GUILD_ID, {}))
                      .get(command, {}))
        for param_name, parameter_def in (
                loaded.game_parameters
                .get(guild_id,
                     loaded.game_parameters.get(constants.DEFAULT_GUILD_ID, {}))
                .get(command, {}).items()):
            parameter = await models.GameParameter.create(
                game=game, name=param_name,
                display_name=parameter_def.get("display_name", param_name),
                api_field=api_fields.get(param_name))
            for value, display in parameter_def.get("values", {}).items():
                await models.ParameterValue.create(
                    parameter=parameter, value=value, display_name=display)
        # Reserved api_* keys: the game's overrides of the fixed match payload
        # components; they live in game_api_fields but are not parameters.
        for key, field_name in api_fields.items():
            if (key.startswith(constants.API_FIELD_PREFIX)):
                await models.GameApiFieldOverride.create(
                    game=game, key=key, field_name=field_name)


# --------------------------------------------------------------------------- #
# Loading (rows -> LoadedConfig)
# --------------------------------------------------------------------------- #

def _build_game_option(game: models.Game) -> GameOption:
    return GameOption(
        name=game.name,
        command=game.command,
        role=game.role,
        icon=game.icon,
        color=game.color,
        forum=game.forum,
        tag=game.tag,
        visibility=game.visibility,
        message=game.message,
        registration_api=game.registration_api,
        match_api=game.match_api,
        match_url=game.match_url,
        api_token_env_var=game.api_token_env_var,
        website_url=game.website_url,
        registration_url=game.registration_url,
        profile_url=game.profile_url,
        default_max_guests=game.default_max_guests,
    )


async def load_config_from_db() -> LoadedConfig:
    """Load the whole configuration from the database into a LoadedConfig.

    Each table is loaded ordered by insertion id, preserving the config
    files' ordering (slash-command option order, help, autocomplete).
    """
    loaded = LoadedConfig()
    guilds = await models.Guild.all().order_by("guild_id")
    games = await models.Game.all().order_by("id")
    parameters = await models.GameParameter.all().order_by("id")
    values = await models.ParameterValue.all().order_by("id")
    overrides = await models.GameApiFieldOverride.all()
    default_fields = await models.DefaultApiField.all()

    games_by_guild: dict[int, list[models.Game]] = {}
    for game in games:
        games_by_guild.setdefault(game.guild_id, []).append(game)
    parameters_by_game: dict[int, list[models.GameParameter]] = {}
    for parameter in parameters:
        parameters_by_game.setdefault(parameter.game_id, []).append(parameter)
    values_by_parameter: dict[int, list[models.ParameterValue]] = {}
    for value in values:
        values_by_parameter.setdefault(value.parameter_id, []).append(value)
    overrides_by_game: dict[int, list[models.GameApiFieldOverride]] = {}
    for override in overrides:
        overrides_by_game.setdefault(override.game_id, []).append(override)

    for guild in guilds:
        guild_key = guild.guild_id
        guild_id = None if guild_key == constants.DEFAULT_GUILD_ID else guild_key
        guild_config = GuildGamesConfig(guild_id)
        for game in games_by_guild.get(guild_key, []):
            guild_config.games[game.command] = _build_game_option(game)
            game_parameters = parameters_by_game.get(game.id, [])
            game_overrides = overrides_by_game.get(game.id, [])
            if (game_parameters or game_overrides):
                # Only games with a games_parameters.ini section get
                # entries (as in the file parsing).
                params_by_guild = loaded.game_parameters.setdefault(guild_key, {})
                fields_by_guild = loaded.game_api_fields.setdefault(guild_key, {})
                params_by_guild[game.command] = {}
                fields_by_guild[game.command] = {}
            for parameter in game_parameters:
                params_by_guild[game.command][parameter.name] = {
                    "display_name": parameter.display_name or parameter.name,
                    "values": {
                        value.value: value.display_name
                        for value in values_by_parameter.get(parameter.id, [])
                    },
                }
                if (parameter.api_field):
                    fields_by_guild[game.command][parameter.name] = (
                        parameter.api_field)
            for override in game_overrides:
                fields_by_guild[game.command][override.key] = override.field_name
        if (guild_id is None):
            loaded.default_guild_config = guild_config
        else:
            loaded.guilds[guild_id] = guild_config
    loaded.default_api_fields = {field.key: field.field_name for field in default_fields}
    return loaded

# --------------------------------------------------------------------------- #
# Runtime admin edits (persisted to the database, then the cog reloads)
# --------------------------------------------------------------------------- #

def _game_fields(game: models.Game) -> dict:
    """The GameOption fields of a Game row, for copying or updating."""
    return {
        "name": game.name,
        "role": game.role,
        "icon": game.icon,
        "color": game.color,
        "forum": game.forum,
        "tag": game.tag,
        "visibility": game.visibility,
        "message": game.message,
        "registration_api": game.registration_api,
        "match_api": game.match_api,
        "match_url": game.match_url,
        "api_token_env_var": game.api_token_env_var,
        "website_url": game.website_url,
        "registration_url": game.registration_url,
        "profile_url": game.profile_url,
        "default_max_guests": game.default_max_guests,
    }


async def ensure_guild_config(guild_id: int) -> None:
    """Give the guild its own configuration row, seeded from the defaults.

    Guilds without a row fall back to the sentinel default config; the first
    admin edit copies those defaults into a per-guild row so the guild gets a
    complete, independent configuration.
    """
    if (guild_id == constants.DEFAULT_GUILD_ID):
        return
    if (await models.Guild.get_or_none(guild_id=guild_id) is not None):
        return
    async with in_transaction():
        guild = await models.Guild.create(guild_id=guild_id)
        for game in await models.Game.filter(guild_id=constants.DEFAULT_GUILD_ID).order_by("id"):
            new_game = await models.Game.create(
                guild=guild, command=game.command, **_game_fields(game))
            for parameter in await models.GameParameter.filter(
                    game_id=game.id).order_by("id"):
                new_parameter = await models.GameParameter.create(
                    game=new_game, name=parameter.name,
                    display_name=parameter.display_name or parameter.name,
                    api_field=parameter.api_field)
                for value in await models.ParameterValue.filter(
                        parameter_id=parameter.id).order_by("id"):
                    await models.ParameterValue.create(
                        parameter=new_parameter,
                        value=value.value, display_name=value.display_name)
            for override in await models.GameApiFieldOverride.filter(game_id=game.id):
                await models.GameApiFieldOverride.create(
                    game=new_game, key=override.key, field_name=override.field_name)


async def add_game(guild_id: int, command: str, **fields) -> bool:
    """Create a game row; whether it was created (False if it already exists)."""
    _, created = await models.Game.get_or_create(
        guild_id=guild_id, command=command, defaults=fields)
    return created


async def update_game(guild_id: int, command: str, **fields) -> bool:
    """Update an existing game row; whether it existed."""
    game = await models.Game.get_or_none(guild_id=guild_id, command=command)
    if (game is None):
        return False
    for key, value in fields.items():
        setattr(game, key, value)
    await game.save(update_fields=list(fields.keys()))
    return True


async def delete_game(guild_id: int, command: str) -> bool:
    """Delete a game row, cascading to its parameters; whether it existed."""
    return (await models.Game.filter(
        guild_id=guild_id, command=command).delete()) > 0


async def add_parameter(guild_id: int, command: str, name: str,
                        values: dict[str, str],
                        api_field: str | None = None,
                        display_name: str | None = None) -> bool:
    """Add a parameter to a game of a guild; whether it was created.

    ``values`` maps raw value -> display name (see ``utils.parse_param_entries``).
    ``api_field`` is the match API field the values are submitted as;
    None/blank means Discord-only. ``display_name`` is the user-facing
    label; None/blank defaults to ``name``.
    """
    game = await models.Game.get_or_none(guild_id=guild_id, command=command)
    if (game is None):
        return False
    if (await models.GameParameter.get_or_none(
            game_id=game.id, name=name) is not None):
        return False
    parameter = await models.GameParameter.create(
        game=game, name=name, display_name=display_name or name,
        api_field=api_field or None)
    for value, display in values.items():
        await models.ParameterValue.create(
            parameter=parameter, value=value, display_name=display)
    return True


_UNSET = object()


async def update_parameter(guild_id: int, command: str, name: str,
                           values: dict[str, str] | None = None,
                           api_field: str | None | object = _UNSET,
                           display_name: str | None | object = _UNSET) -> bool:
    """Update a game's parameter values and/or API field; whether it existed.

    ``values=None`` keeps the current accepted values; ``api_field=_UNSET``
    keeps the current field. Passing a value (or ``""`` to clear/reset it)
    sets it; same for ``display_name`` (blank resets it to the name).
    """
    game = await models.Game.get_or_none(guild_id=guild_id, command=command)
    if (game is None):
        return False
    parameter = await models.GameParameter.get_or_none(
        game_id=game.id, name=name)
    if (parameter is None):
        return False
    if (values is not None):
        await models.ParameterValue.filter(parameter_id=parameter.id).delete()
        for value, display in values.items():
            await models.ParameterValue.create(
                parameter=parameter, value=value, display_name=display)
    if (api_field is not _UNSET):
        parameter.api_field = api_field or None
        await parameter.save(update_fields=["api_field"])
    if (display_name is not _UNSET):
        parameter.display_name = display_name or name
        await parameter.save(update_fields=["display_name"])
    return True


async def delete_parameter(guild_id: int, command: str, name: str) -> bool:
    """Delete a game's parameter (cascading to its values); whether it existed."""
    game = await models.Game.get_or_none(guild_id=guild_id, command=command)
    if (game is None):
        return False
    return (await models.GameParameter.filter(
        game_id=game.id, name=name).delete()) > 0


