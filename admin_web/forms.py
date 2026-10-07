"""Reading the panel's forms into the arguments the cogs' writers take.

A form shows every value the configuration holds and posts all of them back, so
an emptied field clears it. The one value no form carries is a game's API token
(``views/games.py`` has its own endpoints for it): it is a secret, and it is
never rendered.
"""

from cogs.matchmaking import utils as mm_utils
from cogs.matchmaking import validation
from cogs.matchmaking.config import LFGConfigMixin
from cogs.matchrolls import validation as rolls_validation

# The colour picker posts #rrggbb and needs a value; no colour is stored empty.
NO_COLOR = "#ffffff"


def color_set(stored: str | None) -> bool:
    """Whether a stored colour is one the picker can show (and the box ticked)."""
    return mm_utils.parse_color(stored) is not None


def color_hex(stored: str | None) -> str:
    """The ``#rrggbb`` a colour picker shows for a stored colour."""
    colour = mm_utils.parse_color(stored)
    return f"#{colour.value:06x}" if (colour is not None) else NO_COLOR


def color_stored(value: str) -> str | None:
    """The stored decimal for a posted colour, or None when it is unreadable."""
    colour = mm_utils.parse_color(value)
    return str(colour.value) if (colour is not None) else None


def game_form(form, command: str | None = None,
              existing_commands=()) -> tuple[dict, list[str]]:
    """The values a game form posts, as ``add_game``/``update_game`` arguments.

    ``command`` is the game being edited; None when the form adds a new one.
    Returns ``(arguments, errors)``. The arguments carry every field the form
    shows, so the caller writes the game exactly as it was submitted.
    """
    editing = command is not None
    errors: list[str] = []
    new_command = _text(form, "command")
    if (editing):
        if (not new_command):
            errors.append("`command` is required: delete the game to remove it.")
        elif (new_command != command):
            if (not validation.is_valid_command_name(new_command)):
                errors.append(validation.invalid_command_message(new_command))
            elif (new_command in set(existing_commands)):
                # The rule validation.rename_error applies to /games, worded
                # for a panel that has no options to omit.
                errors.append(f"`{new_command}` is already configured for "
                              "this server.")
    elif (not validation.is_valid_command_name(new_command)):
        errors.append(validation.invalid_command_message(new_command))
    name = _text(form, "name")
    error = validation.game_name_value_error(name)
    if (error):
        errors.append(error)
    channel = _text(form, "channel")
    forum = _text(form, "forum")
    error = validation.mention_error(None, channel, forum)
    if (error):
        errors.append(error)
    max_players = _text(form, "max_players")
    default_max_guests = None
    if (max_players):
        default_max_guests = LFGConfigMixin.parse_default_max_guests(max_players)
        if (default_max_guests is None):
            errors.append("`max_players` must be between 2 and 100.")
    visibility = _text(form, "visibility")
    error = validation.visibility_error(visibility)
    if (error):
        errors.append(error)
    color = _color(form, errors)
    api_fields, api_errors = _api_fields(form)
    errors.extend(api_errors)
    fields = {
        "name": name,
        "icon": _text(form, "icon"),
        "color": color,
        "channel": channel or None,
        "forum": forum or None,
        "tag": _text(form, "tag") or None,
        "visibility": visibility or None,
        "message": _text(form, "message") or None,
        "registration_api": _text(form, "registration_api") or None,
        "match_api": _text(form, "match_api") or None,
        "match_url": _text(form, "match_url") or None,
        "website_url": _text(form, "website_url") or None,
        "registration_url": _text(form, "registration_url") or None,
        "profile_url": _text(form, "profile_url") or None,
        "default_max_guests": default_max_guests,
    }
    arguments = {"fields": fields, "api_fields": api_fields}
    if (editing):
        # A command only changes on an explicit rename; the game keeps its own.
        arguments["new_command"] = (new_command
                                    if new_command != command else None)
    else:
        arguments["command"] = new_command
    return arguments, errors


def ping_form(form) -> tuple[dict, list[str]]:
    """The role or the member a ping form names, as the mention to store.

    The role select posts a role mention, stored as it stands. The member field
    takes a mention as it stands, or a name, which the caller resolves against
    the server's members (see ``views/games.py``).
    """
    role = _text(form, "role")
    member = _text(form, "member")
    if (role and member):
        return {}, ["Give either a role or a member, not both."]
    if (role):
        error = validation.mention_error(role, None, None)
        return ({}, [error]) if (error) else ({"mention": role}, [])
    if (not member):
        return {}, ["Give a role or a member, or use Clear the ping."]
    if (validation.is_role_or_user_mention(member)):
        return {"mention": member}, []
    return {"name": member}, []


def parameter_form(form) -> tuple[dict, list[str]]:
    """The values a parameter form posts, as ``add``/``update`` arguments.

    An emptied display name or API field means "use the default": both are
    stored blank, which is how the cog reads them.
    """
    name = _text(form, "name")
    raw_values = _text(form, "values")
    api_field = _text(form, "api_field")
    display_name = _text(form, "display_name")
    error = validation.parameter_error(name, raw_values, api_field, display_name)
    if (error):
        return {}, [error]
    return {"name": name, "values": mm_utils.parse_param_entries(raw_values),
            "api_field": api_field or None,
            "display_name": display_name}, []


def copy_form(form, source_command: str,
              source_name: str) -> tuple[dict, list[str]]:
    """The new command and name a copy form posts, checked against its source."""
    command = _text(form, "command")
    name = _text(form, "name")
    error = validation.copy_error(command, name, source_command, source_name)
    if (error):
        return {}, [error]
    return {"command": command, "name": name}, []


def parameter_values_text(values: dict[str, str]) -> str:
    """A parameter's accepted values as the text its form shows (and parses back)."""
    return ", ".join(value if (display == value) else f"({value}, {display})"
                     for value, display in values.items())


def token_form(form) -> tuple[str, list[str]]:
    """The API token a token form posts, or an error when it is empty."""
    token = _text(form, "api_token")
    if (not token):
        return "", ["Give a token, or use the Clear button to remove it."]
    return token, []


def category_form(form) -> tuple[dict, list[str]]:
    """The values a roll category form posts, as the cog's writer takes them.

    Returns ``(arguments, errors)``. The item set is the comma-separated form
    the commands take; both rules are the shared validators' own words.
    """
    errors: list[str] = []
    name = _text(form, "name")
    error = rolls_validation.category_name_error(name, "name")
    if (error):
        errors.append(error)
    items, error = rolls_validation.item_names_error(_text(form, "items"))
    if (error):
        errors.append(error)
    if (errors):
        return {}, errors
    return {"name": name, "item_names": items}, []


def variant_form(form) -> tuple[dict, list[str]]:
    """The fields a description-variant form posts, as the writer takes them.

    Every field is set from the form, so a blank one clears the value (the
    commands' ``-`` sentinel is Discord's way of posting that blank; the panel
    posts the blank). Only the text's length and the URLs are checked by the
    cog's rules.
    """
    errors: list[str] = []
    text = _variant_text(form, errors)
    color = _variant_color(form, errors)
    image = _variant_url(form, errors, "image_url", "image")
    thumbnail = _variant_url(form, errors, "thumbnail_url", "thumbnail")
    if (not errors and not (text or color is not None or image or thumbnail)):
        errors.append("Give the variant a text, a colour or an image.")
    return {"description": text, "color": color, "image_url": image,
            "thumbnail_url": thumbnail}, errors


def _variant_text(form, errors: list[str]) -> str:
    """The text a variant form posts, empty when it has none."""
    text = _text(form, "description")
    if (not text):
        return ""
    parsed, error = rolls_validation.parse_text(text)
    if (error):
        errors.append(error)
        return ""
    return parsed


def _variant_color(form, errors: list[str]) -> int | None:
    """The colour a variant form posts, None unless its box is ticked.

    A colour input always posts a value, so ticking the box is what says the
    value is meant: unticked, the bot rolls a random colour for the variant.
    """
    if (not form.get("use_color")):
        return None
    stored = color_stored(_text(form, "color"))
    if (stored is None):
        errors.append("`color` must be a colour like #1a2b3c.")
        return None
    return int(stored)


def _variant_url(form, errors: list[str], field: str, label: str) -> str | None:
    """The URL a variant form posts for one of its images; blank clears it."""
    posted = _text(form, field)
    if (not posted):
        return None
    parsed, error = rolls_validation.parse_url(posted, label)
    if (error):
        errors.append(error)
        return None
    return parsed


def _color(form, errors: list[str]) -> str:
    """The stored colour a form posts, empty unless its box is ticked.

    A colour input always posts a value, so ticking the box is what says the
    value is meant: leaving it unticked keeps the game colourless.
    """
    if (not form.get("use_color")):
        return ""
    posted = _text(form, "color")
    stored = color_stored(posted) if (posted) else None
    if (stored is None):
        errors.append("`color` must be a colour like #1a2b3c.")
        return ""
    return stored


def _api_fields(form) -> tuple[dict[str, str | None], list[str]]:
    """The reserved api_* overrides a form posts; empty means "use the default"."""
    api_fields: dict[str, str | None] = {}
    errors: list[str] = []
    for argument, key in validation.API_FIELD_ARGUMENTS.items():
        value = _text(form, argument)
        if (value and not validation.is_valid_api_field(value)):
            errors.append(f"`{argument}` must be a field name: letters, "
                          "digits or underscores.")
        else:
            api_fields[key] = value or None
    return api_fields, errors


def _text(form, field: str) -> str:
    """A posted field as text, with the empty string for anything else."""
    value = form.get(field)
    return value.strip() if isinstance(value, str) else ""
