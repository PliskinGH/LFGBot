"""Tortoise ORM models for the bot's database-backed configuration.

The tables mirror ``config/games.ini`` (guilds and their games) and
``config/games_parameters.ini`` (per-game parameters and fixed match payload
field names); ``cogs/matchmaking/db_config.py`` maps rows <-> config objects.
The ``Roll*`` tables mirror ``config/rolls.ini`` (guilds, their roll
categories and items) and ``config/rolls_descriptions.json`` (one embed per
item); ``cogs/matchrolls/db_config.py`` maps
those rows <-> config objects. Rows are seeded in config-file order and
loaded back ordered by their insertion ``id``, preserving the ordering the
cogs rely on.
"""

from tortoise import fields, models


class Guild(models.Model):
    """A Discord guild's configuration rows; sentinel guild id 0 = the [DEFAULT] config."""

    guild_id = fields.BigIntField(primary_key=True)

    games: fields.ReverseRelation["Game"]
    roll_categories: fields.ReverseRelation["RollCategory"]

    class Meta:
        table = "guilds"


class Game(models.Model):
    """One game configured for a guild: a row per ``GameOption``."""

    id = fields.IntField(primary_key=True)
    guild = fields.ForeignKeyField("models.Guild", related_name="games")
    command = fields.TextField()
    name = fields.TextField(default="")
    role = fields.TextField(default="")
    icon = fields.TextField(default="")
    color = fields.TextField(default="")
    # Discord references and website endpoints, verbatim from the config files.
    forum = fields.TextField(null=True)
    channel = fields.TextField(null=True)
    tag = fields.TextField(null=True)
    visibility = fields.TextField(null=True)
    message = fields.TextField(null=True)
    registration_api = fields.TextField(null=True)
    match_api = fields.TextField(null=True)
    match_url = fields.TextField(null=True)
    api_token = fields.TextField(default="", db_default="")
    website_url = fields.TextField(null=True)
    registration_url = fields.TextField(null=True)
    profile_url = fields.TextField(null=True)
    # Parsed default guest count (max_players - 1).
    default_max_guests = fields.IntField(null=True)

    parameters: fields.ReverseRelation["GameParameter"]
    api_field_overrides: fields.ReverseRelation["GameApiFieldOverride"]

    class Meta:
        table = "games"
        unique_together = (("guild", "command"),)


class GameParameter(models.Model):
    """An optional slash-command parameter of a game."""

    id = fields.IntField(primary_key=True)
    game = fields.ForeignKeyField("models.Game", related_name="parameters")
    name = fields.TextField()
    # User-facing label (defaults to the name). The name itself stays a valid
    # slash-command option name (lowercase, no spaces), so the friendlier
    # label is stored separately; blank means "use the name". The db_default
    # backfills existing rows when the column is added by a migration.
    display_name = fields.TextField(default="", db_default="")
    # Match API field the parameter is submitted as; None = Discord-only.
    api_field = fields.TextField(null=True)

    values: fields.ReverseRelation["ParameterValue"]

    class Meta:
        table = "game_parameters"
        unique_together = (("game", "name"),)


class ParameterValue(models.Model):
    """One acceptable value of a game parameter, with its display name."""

    id = fields.IntField(primary_key=True)
    parameter = fields.ForeignKeyField("models.GameParameter", related_name="values")
    value = fields.TextField()
    display_name = fields.TextField()

    class Meta:
        table = "game_parameter_values"
        unique_together = (("parameter", "value"),)


class GameApiFieldOverride(models.Model):
    """A game's override of a fixed match payload component (api_* key)."""

    id = fields.IntField(primary_key=True)
    game = fields.ForeignKeyField("models.Game", related_name="api_field_overrides")
    key = fields.TextField()
    field_name = fields.TextField()

    class Meta:
        table = "game_api_field_overrides"
        unique_together = (("game", "key"),)


class DefaultApiField(models.Model):
    """The fixed match payload component field names from the [DEFAULT]
    section of games_parameters.ini (api_* keys), inherited by every game."""

    key = fields.CharField(max_length=255, primary_key=True)
    field_name = fields.TextField()

    class Meta:
        table = "default_api_fields"


class RollCategory(models.Model):
    """One roll category of a guild; its set is given by its active items."""

    id = fields.IntField(primary_key=True)
    guild = fields.ForeignKeyField(
        "models.Guild", related_name="roll_categories")
    name = fields.TextField()

    roll_items: fields.ReverseRelation["RollItem"]

    class Meta:
        table = "roll_categories"
        unique_together = (("guild", "name"),)


class RollItem(models.Model):
    """One rollable item of a category: a row per name ever configured.

    ``active`` is set (or reactivated) when the name is part of the
    category's set; inactive rows keep their name and description variants,
    so re-adding the name restores its flavors.
    """

    id = fields.IntField(primary_key=True)
    category = fields.ForeignKeyField(
        "models.RollCategory", related_name="roll_items")
    name = fields.TextField()
    active = fields.BooleanField(default=True, db_default=True)

    descriptions: fields.ReverseRelation["RollDescription"]

    class Meta:
        table = "roll_items"


class RollDescription(models.Model):
    """One roll description embed of an item, from rolls_descriptions.json.

    ``description`` may be blank and the image/thumbnail URLs unset, as in
    the JSON; a null ``color`` rolls a random colour at runtime.
    """

    id = fields.IntField(primary_key=True)
    item = fields.ForeignKeyField(
        "models.RollItem", related_name="descriptions")
    description = fields.TextField(default="", db_default="")
    color = fields.IntField(null=True)
    image_url = fields.TextField(null=True)
    thumbnail_url = fields.TextField(null=True)

    class Meta:
        table = "roll_descriptions"
