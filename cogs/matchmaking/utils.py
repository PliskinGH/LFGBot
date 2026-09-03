"""Helpers for the matchmaking cog: games_parameters.ini parsing, parameter
value/display handling, and LFG embed/thread helpers.
"""

import configparser

import discord

from common.utils import get_id_from_mention

from . import constants


def parse_game_parameters(parameters_config: configparser.ConfigParser | None,
                          ) -> tuple[dict[str, dict[str, dict[str, dict[str, str]]]],
                                     dict[str, dict[str, str]],
                                     dict[str, str]]:
    """Parse a game-parameters config into its component maps.

    Each section is a game command; each key is a parameter name. Its
    value is an optional match API field name followed by a colon, then
    the acceptable values: either a comma-separated list or a list of
    (value, display name) pairs (see ``parse_param_entries``).
    Parameters are returned as ``{param_name: {"display_name": label,
    "values": {value: display_name}}}`` maps in ``game_parameters`` (the
    label defaults to the name: the config files have no display-name
    syntax); the API field name (when present) is returned per parameter
    in ``game_api_fields``.

    The [DEFAULT] section's api_* keys declare the fixed payload
    component field names (title, thread link, participants) for every
    game — including games without a section here — and are returned in
    ``default_api_fields``.
    """
    game_parameters: dict[str, dict[str, dict[str, dict[str, str]]]] = {}
    game_api_fields: dict[str, dict[str, str]] = {}
    default_api_fields: dict[str, str] = {}
    if (parameters_config is None):
        return game_parameters, game_api_fields, default_api_fields
    default_api_fields = {
        key: value.strip() for key, value in parameters_config.defaults().items()
        if key.startswith(constants.API_FIELD_PREFIX)
    }
    for game_command in parameters_config.sections():
        game_parameters[game_command] = {}
        game_api_fields[game_command] = {}
        for param_name, raw_value in parameters_config.items(game_command):
            if (param_name.startswith(constants.API_FIELD_PREFIX)):
                # Reserved key: API field name for a fixed match payload
                # component (title, thread link, participants...).
                game_api_fields[game_command][param_name] = raw_value.strip()
                continue
            api_field, value_list = _split_api_field(raw_value)
            value_display = parse_param_entries(value_list)
            if (value_display):
                game_parameters[game_command][param_name] = {
                    "display_name": param_name,
                    "values": value_display,
                }
                if (api_field):
                    game_api_fields[game_command][param_name] = api_field
    return game_parameters, game_api_fields, default_api_fields


def _split_api_field(raw_value: str) -> tuple[str, str]:
    """Split ``api_field: value list`` into (api_field, value list).

    The API field is everything before the first colon; the value list is
    the rest. A value without a colon has no API field (the parameter is
    only surfaced in Discord, not sent to the match API).
    """
    parts = raw_value.split(":", 1)
    if (len(parts) == 2):
        return parts[0].strip(), parts[1]
    return "", raw_value

def parse_param_entries(raw_value: str) -> dict[str, str]:
    """Parse a game-parameter config line into a ``{value: display_name}`` map.

    Accepts both bare comma-separated values (``a, b``, display == value)
    and parenthesized value/display pairs (``(a, A), (b, B)``). No quoting
    is required: parentheses delimit each pair and the first comma inside
    them separates value from display, so display names may contain commas.
    """
    value_display = {}
    for entry in _split_param_entries(raw_value):
        if (not entry):
            continue
        if (entry.startswith("(") and entry.endswith(")")):
            inner = entry[1:-1].strip()
            value, _, display = inner.partition(",")
            value = value.strip()
            display = display.strip()
        else:
            value = display = entry.strip()
        if (value):
            value_display[value] = display or value
    return value_display


def _split_param_entries(raw_value: str) -> list[str]:
    """Split a game-parameter config line on top-level commas (not inside parens)."""
    entries = []
    depth = 0
    current = []
    for char in raw_value:
        if (char == "("):
            depth += 1
        elif (char == ")"):
            depth -= 1
        if (char == "," and depth == 0):
            entries.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if (current):
        entries.append("".join(current).strip())
    return entries


def normalize_param_values(values: list[str], value_display: dict[str, str]) -> list[str]:
    """Resolve parameter display names back to their raw values.

    ``values`` may mix raw values and display names (e.g. when a Discord
    client commits an autocomplete choice by writing its name). Raw values
    and unknown tokens are left unchanged so callers can validate them.
    """
    display_to_value = {
        display.lower(): value for value, display in value_display.items()
    }
    normalized = []
    for value in values:
        if (value in value_display):
            normalized.append(value)
        elif (value.lower() in display_to_value):
            normalized.append(display_to_value[value.lower()])
        else:
            normalized.append(value)
    return normalized


def render_param_values(values: list[str], value_display: dict[str, str]) -> list[str]:
    """Map parameter raw values to their display names for user-facing rendering.

    Tokens that are already display names (or unknown) are kept as-is.
    """
    display_to_value = {
        display.lower(): value for value, display in value_display.items()
    }
    rendered = []
    for value in values:
        if (value in value_display):
            rendered.append(value_display[value])
        elif (value.lower() in display_to_value):
            rendered.append(value)
        else:
            rendered.append(value)
    return rendered


def format_accepted_values(value_display: dict[str, str]) -> str:
    """Render a ``{value: display_name}`` map as a comma-separated summary.

    Used in help text and error messages, e.g.
    ``adset (Advanced), standard``. Display names equal to their value are
    shown bare.
    """
    return ", ".join(
        display if display == value else f"{value} ({display})"
        for value, display in value_display.items()
    )


def host_id_from_message(message: discord.Message | None) -> int | None:
    """The LFG host's user id recorded in ``message``'s embed, if any.

    Returns None when the message has no embed or no "Host" field.
    """
    if (message is None or not message.embeds):
        return None
    for field in message.embeds[0].fields:
        if (field.name == "Host"):
            return get_id_from_mention(field.value)
    return None


async def fetch_host_id(thread: discord.Thread) -> int | None:
    """The LFG host's user id recorded in ``thread``'s LFG message.

    Where the LFG embed lives depends on how the thread was created:

    * Forum posts keep it as the thread's own starter message, whose ID is
      the thread's ID, so it is fetched from the thread.
    * A thread created under a message shares that message's ID, but the
      message remains in the parent channel: it can only be fetched through
      ``thread.parent``, not through the thread channel itself.
    * A private thread created from a channel has no parent message; the bot
      posts the LFG embed as the first message of the thread, which is found
      through the thread history.

    Returns None when no LFG message (or no "Host" field) could be found.
    """
    # The starter message may already be cached: forum posts and message
    # threads both reuse the thread ID for the starter message.
    message = getattr(thread, "starter_message", None)
    if (message is not None):
        host_id = host_id_from_message(message)
        if (host_id is not None):
            return host_id

    parent = getattr(thread, "parent", None)
    if (parent is None or parent.type == discord.ChannelType.forum):
        # Forum post: the LFG embed is the thread's own starter message.
        channel = thread
    else:
        # Message thread: the LFG message sits in the parent channel and its
        # ID is the thread's ID.
        channel = parent

    try:
        message = await channel.fetch_message(thread.id)
    except (discord.HTTPException, AttributeError):
        message = None
    if (message is not None):
        host_id = host_id_from_message(message)
        if (host_id is not None):
            return host_id

    # Last resort (private threads started from a channel): scan the thread
    # history for the LFG embed the bot posted on creation.
    try:
        async for message in thread.history(limit=1, oldest_first=True):
            host_id = host_id_from_message(message)
            if (host_id is not None):
                return host_id
    except discord.HTTPException:
        pass
    return None

