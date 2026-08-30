"""INI <-> database mapping for the matchmaking cog's configuration.

With ``DATABASE_URL`` set, the database is the source of truth: seeded from
the config files on first initialization, loaded from it afterwards.
``LoadedConfig`` mirrors the structures ``ConfigMixin`` builds from the
files, so the cog consumes both sources identically; seeding preserves the
config files' ordering (slash-command option order, help, autocomplete).
"""

import configparser

from tortoise.transactions import in_transaction

from common import common
from db import models

from . import constants
from . import utils
from .config import ConfigMixin
from .models import GameOption, GuildGamesConfig

# Sentinel guild id storing the [DEFAULT] section's games (Discord snowflakes
# are always positive, so 0 never collides).
DEFAULT_GUILD_ID = 0


class LoadedConfig:
    """The in-memory configuration structures ``Matchmaking`` consumes."""

    def __init__(self):
        self.guilds: dict[int, GuildGamesConfig] = {} # dict of guild_id -> GuildGamesConfig
        self.default_guild_config = GuildGamesConfig(None)
        # game_command -> { parameter_name -> { value: display_name } }
        self.game_parameters: dict[str, dict[str, dict[str, str]]] = {}
        # game_command -> { parameter_name or reserved api_* key -> match API field name }
        self.game_api_fields: dict[str, dict[str, str]] = {}
        # Fixed payload component field names from the [DEFAULT] section of
        # games_parameters.ini; inherited by every game (including games
        # without a section there), overridden by each game's own api_* keys.
        self.default_api_fields: dict[str, str] = {}


def _get_guild_id(config: configparser.ConfigParser, section: str) -> int | None:
    """The section's ``ID`` key, or None when absent or blank."""
    try:
        return config.getint(section, common.CONFIG_ID, fallback=None)
    except ValueError:
        return None


def loaded_config_from_ini(config: configparser.ConfigParser,
                           game_parameters: configparser.ConfigParser) -> LoadedConfig:
    """Parse the games config files into a LoadedConfig (file-based mode)."""
    loaded = LoadedConfig()
    ConfigMixin._load_guild_config(loaded.default_guild_config, config, common.CONFIG_DEFAULT)
    for guild in config.sections():
        guild_id = _get_guild_id(config, guild)
        if (guild_id is None):
            continue
        guild_config = GuildGamesConfig(guild_id)
        ConfigMixin._load_guild_config(guild_config, config, guild)
        loaded.guilds[guild_id] = guild_config
    (loaded.game_parameters, loaded.game_api_fields,
     loaded.default_api_fields) = utils.parse_game_parameters(game_parameters)
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
        await _create_guild(DEFAULT_GUILD_ID, loaded.default_guild_config, loaded)
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
        api_fields = loaded.game_api_fields.get(command, {})
        for param_name, value_display in loaded.game_parameters.get(command, {}).items():
            parameter = await models.GameParameter.create(
                game=game, name=param_name, api_field=api_fields.get(param_name))
            for value, display in value_display.items():
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
        guild_id = None if guild.guild_id == DEFAULT_GUILD_ID else guild.guild_id
        guild_config = GuildGamesConfig(guild_id)
        for game in games_by_guild.get(guild.guild_id, []):
            guild_config.games[game.command] = _build_game_option(game)
            game_parameters = parameters_by_game.get(game.id, [])
            game_overrides = overrides_by_game.get(game.id, [])
            if (game_parameters or game_overrides):
                # Only games with a games_parameters.ini section get
                # entries (as in the file parsing).
                loaded.game_parameters[game.command] = {}
                loaded.game_api_fields[game.command] = {}
            for parameter in game_parameters:
                loaded.game_parameters[game.command][parameter.name] = {
                    value.value: value.display_name
                    for value in values_by_parameter.get(parameter.id, [])
                }
                if (parameter.api_field):
                    loaded.game_api_fields[game.command][parameter.name] = (
                        parameter.api_field)
            for override in game_overrides:
                loaded.game_api_fields[game.command][override.key] = override.field_name
        if (guild_id is None):
            loaded.default_guild_config = guild_config
        else:
            loaded.guilds[guild_id] = guild_config
    loaded.default_api_fields = {field.key: field.field_name for field in default_fields}
    return loaded


