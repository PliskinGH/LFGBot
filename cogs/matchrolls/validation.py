"""Validation of the /rollsets admin inputs, shared by the commands and the web panel."""

from common import constants as common_constants


def category_name_error(value: str, fallback: str) -> str | None:
    """An error message when a category name is invalid, else None."""
    name = value.strip()
    if (not name or len(name) > 50 or "\n" in name or "\r" in name):
        return f"`{fallback}` must be 1-50 characters without newlines."
    if (name == common_constants.CONFIG_ID.lower()):
        return (f"`{fallback}` cannot be "
                f"`{common_constants.CONFIG_ID.lower()}` (reserved).")
    return None


def item_names_error(value: str) -> tuple[list[str] | None, str | None]:
    """(names, error): the parsed item names, or an error message."""
    names = [item.strip() for item in value.split(",")]
    if (not names or not names[0]):
        return None, "`items` requires at least one item."
    for name in names:
        if (not name or len(name) > 50 or "\n" in name or "\r" in name):
            return None, ("`items` must be a comma-separated list of "
                          "1-50 character names.")
    if (len(set(names)) != len(names)):
        return None, "`items` cannot contain duplicate names."
    return names, None


def parse_color(value: str | None) -> tuple[int | None, str | None]:
    """(color, error): ``-`` clears the color; None keeps it."""
    if (value is None or value == common_constants.RESET_SENTINEL):
        return None, None
    try:
        color = int(value)
    except ValueError:
        return None, "`color` must be an integer 0-16777215, or `-` to clear it."
    if (not (0 <= color <= 0xFFFFFF)):
        return None, "`color` must be an integer 0-16777215, or `-` to clear it."
    return color, None


def parse_url(value: str | None, name: str) -> tuple[str | None, str | None]:
    """(url, error): ``-`` clears the value; None keeps it."""
    if (value is None or value == common_constants.RESET_SENTINEL):
        return None, None
    value = value.strip()
    if (not value or len(value) > 2048):
        return None, (f"`{name}` must be a URL of at most 2048 characters, "
                      "or `-` to clear it.")
    return value, None


def parse_text(value: str | None) -> tuple[str | None, str | None]:
    """(text, error): ``-`` clears the text; None keeps it."""
    if (value is None):
        return None, None
    if (value == common_constants.RESET_SENTINEL):
        return "", None
    if (not value or len(value) > common_constants.EMBED_DESCRIPTION_LIMIT):
        return None, (f"`text` must be 1-{common_constants.EMBED_DESCRIPTION_LIMIT} "
                      "characters, or `-` to clear it.")
    return value, None
