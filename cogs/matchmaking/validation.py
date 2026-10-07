"""Validation of the /games admin inputs, shared by the commands and the web panel."""

from collections.abc import Iterable
from typing import Optional

from common import constants as common_constants

from . import constants
from . import utils
from .config import LFGConfigMixin

# Reserved match-payload component fields: /games add|update argument name
# -> canonical api_* key stored as a per-game override of the default payload
# field names. Unlike the game columns they are not Game model fields.
API_FIELD_ARGUMENTS = {
    "title_field": constants.API_TITLE_FIELD_KEY,
    "table_talk_url_field": constants.API_TABLE_TALK_URL_FIELD_KEY,
    "participants_field": constants.API_PARTICIPANTS_FIELD_KEY,
    "discord_username_field": constants.API_DISCORD_USERNAME_FIELD_KEY,
}


def is_valid_command_name(name: str) -> bool:
    """Whether ``name`` can become a Discord slash command."""
    return bool(common_constants.COMMAND_NAME_RE.match(name))


def invalid_command_message(name: str) -> str:
    """The message for a command name Discord would refuse."""
    return (f"`{name}` is not a valid slash command name: use 1-32 "
            "lowercase letters, digits or underscores.")


def game_name_value_error(name: str) -> str | None:
    """An error message when a display name is too long or breaks title parsing."""
    if (len(name) > constants.GAME_NAME_MAX):
        return f"`name` must be at most {constants.GAME_NAME_MAX} characters."
    if (constants.GAME_NAME_INVALID_RE.search(name)):
        return "`name` cannot contain \"game:\": it would break the LFG title parsing."
    return None


def game_name_error(name: str | None) -> str | None:
    """An error message when a game display name is invalid, else None."""
    if (not name):
        return None
    if (name == common_constants.RESET_SENTINEL):
        return "`name` cannot be `-`: the display name has no reset; just omit the option to keep it."
    return game_name_value_error(name)


def is_valid_api_field(value: str) -> bool:
    """Whether a value can be a match API field name."""
    return bool(common_constants.API_FIELD_RE.match(value))


def parameter_error(name: str, values: str | None,
                    api_field: str | None = None,
                    display_name: str | None = None) -> str | None:
    """An error message when a parameter name/values/api_field are invalid, else None."""
    if (not common_constants.COMMAND_NAME_RE.match(name)):
        return "`name` must be 1-32 lowercase letters, digits or underscores."
    if (name.startswith(constants.API_FIELD_PREFIX)):
        return f"`name` cannot start with `{constants.API_FIELD_PREFIX}` (reserved)."
    if (values is not None and not utils.parse_param_entries(values or "")):
        return "`values` must contain at least one value."
    # Blank (empty string) is valid: it resets the API field (db_config
    # turns "" into NULL); on add it means "no field". The update command
    # accepts "-" as the reset sentinel, since Discord cannot send "".
    if (api_field and not is_valid_api_field(api_field)):
        return ("`api_field` must be a non-empty field name (letters, digits,"
                " underscores), or `-` to reset it.")
    if (display_name is not None and display_name != "" and (
            not display_name.strip() or len(display_name) > 50
            or "\n" in display_name or "\r" in display_name)):
        return "`display_name` must be 1-50 characters without newlines."
    return None


def is_role_or_user_mention(value: str) -> bool:
    """Whether a value is the role or user mention a ping accepts."""
    return bool(common_constants.ROLE_MENTION_RE.match(value))


def mention_error(role: str | None, channel: str | None,
                  forum: str | None = None) -> str | None:
    """An error message when role/channel/forum are not Discord mentions, else None.

    The values must be valid mentions (roles: role/user mentions;
    LFG channels and forums: channel mentions); anything else would not
    resolve at runtime.
    """
    if (role and not is_role_or_user_mention(role)):
        return "`role` must be a role or user mention."
    if (channel and not common_constants.CHANNEL_MENTION_RE.match(channel)):
        return "`channel` must be a channel mention."
    if (forum and not common_constants.CHANNEL_MENTION_RE.match(forum)):
        return "`forum` must be a channel mention."
    return None


def api_fields_error(
        values: dict[str, Optional[str]]
) -> tuple[dict[str, Optional[str]], Optional[str]]:
    """The reserved api_* overrides from /games add|update arguments.

    Returns ``(api_fields, error)``: a mapping of canonical api_* keys to
    the requested field name (``None`` = clear the override, falling back
    to the default), or an error message when a value is malformed.
    Arguments left out (None) keep the current override untouched.
    """
    api_fields = {}
    for argument, key in API_FIELD_ARGUMENTS.items():
        value = values.get(argument)
        if (value is None):
            continue
        if (value == common_constants.RESET_SENTINEL):
            api_fields[key] = None
        elif (not is_valid_api_field(value)):
            return {}, (f"`{argument}` must be a non-empty field name "
                        "(letters, digits, underscores), or `-` to reset "
                        "it to the default.")
        else:
            api_fields[key] = value
    return api_fields, None


def visibility_error(value: str | None) -> str | None:
    """An error message when a thread visibility is not a thread type, else None.

    The cog reads it as an integer (0 = private thread); a value it cannot read
    would raise at thread creation, so it is refused here instead.
    """
    if (not value):
        return None
    try:
        int(value)
    except ValueError:
        return ("`visibility` must be a number: `0` for private threads, "
                "anything else for public ones.")
    return None


def game_fields(
        name="", role="", icon="", color="",
        channel=None, forum=None, tag=None, visibility=None, message=None,
        registration_api=None, match_api=None, match_url=None,
        api_token="",
        website_url=None, registration_url=None, profile_url=None,
        max_players=None,
) -> tuple[dict, str | None]:
    """The Game fields from add options; returns (fields, error message).

    ``api_token`` is the token VALUE (a secret, never displayed): config
    files resolve their env var at load time, admins set it via /games.
    """
    error = mention_error(role, channel, forum)
    if (error is not None):
        return None, error
    error = game_name_error(name)
    if (error is not None):
        return None, error
    error = visibility_error(visibility)
    if (error is not None):
        return None, error
    default_max_guests = None
    if (max_players is not None):
        default_max_guests = LFGConfigMixin.parse_default_max_guests(str(max_players))
        if (default_max_guests is None):
            return None, "`max_players` must be between 2 and 100."
    fields = {
        "name": name,
        "role": role,
        "icon": icon,
        "color": color,
        "channel": channel,
        "forum": forum,
        "tag": tag,
        "visibility": visibility,
        "message": message,
        "registration_api": registration_api,
        "match_api": match_api,
        "match_url": match_url,
        "api_token": api_token,
        "website_url": website_url,
        "registration_url": registration_url,
        "profile_url": profile_url,
        "default_max_guests": default_max_guests,
    }
    return fields, None


def updated_fields(
        name=None, role=None, icon=None, color=None,
        channel=None, forum=None, tag=None, visibility=None, message=None,
        registration_api=None, match_api=None, match_url=None,
        api_token=None,
        website_url=None, registration_url=None, profile_url=None,
        max_players=None,
) -> tuple[dict, str | None]:
    """The Game fields to change from update options; (fields, error).

    Only the provided options are touched; omitted ones keep their value.
    ``api_token`` accepts ``-`` as the reset sentinel (Discord cannot send
    an empty string), which clears the token.
    """
    error = mention_error(role, channel, forum)
    if (error is not None):
        return None, error
    error = game_name_error(name)
    if (error is not None):
        return None, error
    error = visibility_error(visibility)
    if (error is not None):
        return None, error
    provided = {
        "name": name, "role": role, "icon": icon, "color": color,
        "channel": channel, "forum": forum, "tag": tag,
        "visibility": visibility, "message": message,
        "registration_api": registration_api, "match_api": match_api,
        "match_url": match_url, "website_url": website_url,
        "registration_url": registration_url, "profile_url": profile_url,
    }
    fields = {key: value for key, value in provided.items() if value is not None}
    if (api_token is not None):
        fields["api_token"] = ("" if (api_token == common_constants.RESET_SENTINEL)
                               else api_token)
    if (max_players is not None):
        default_max_guests = LFGConfigMixin.parse_default_max_guests(str(max_players))
        if (default_max_guests is None):
            return None, "`max_players` must be between 2 and 100."
        fields["default_max_guests"] = default_max_guests
    return fields, None


def rename_error(new_command: str, current_command: str,
                 existing_commands: Iterable[str] = ()) -> str | None:
    """An error message when a new game command name is unusable, else None."""
    if (not is_valid_command_name(new_command)):
        return invalid_command_message(new_command)
    if (new_command == current_command):
        return "`new_command` must differ from `command`."
    if (new_command in set(existing_commands)):
        return (f"`{new_command}` is already configured for this server; "
                "use `/games update` to change it instead.")
    return None


def copy_error(new_command: str, new_name: str, source_command: str,
               source_name: str) -> str | None:
    """An error message when a copy's command or display name is unusable, else None.

    A command that is already configured is left to the copy itself to refuse:
    only the write knows which row it would collide with.
    """
    if (not is_valid_command_name(new_command)):
        return invalid_command_message(new_command)
    if (new_command == source_command):
        return f"`command` must differ from the copied game `{source_command}`."
    if (new_name == source_name):
        return "`name` must differ from the copied game's display name."
    return None
