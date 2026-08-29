# -*- coding: utf-8 -*-
"""
Created on Tue Mar 29 18:21:04 2022

@author: Pliskin
"""

import re

from common.common import *

def split_config_list(value):
    if (value is None):
        return []
    return [x.strip() for x in value.split(',')]

def get_guild_from_config(config, guild_id):
    found = False
    
    for guild in config.sections():
        found = (guild_id == config.getint(guild, CONFIG_ID, fallback=None))
        if (found):
            break
    
    if (not(found)):
        guild = CONFIG_DEFAULT
    
    return guild

def parse_intervals(string, cardinal):
    value_list = []
    if not(re.match(r'^[0-9\-\,]*$', string)):
        return value_list
    
    intervals = []
    intervals_str = string.split(',')
    for interval_str in intervals_str:
        bounds = [int(x) for x in interval_str.split('-')]
        if (len(bounds) == 1):
            intervals.append(bounds)
        elif (len(bounds) > 1):
            max_bound = max(bounds)
            min_bound = min(bounds)
            intervals.append([min_bound, max_bound])
    
    if (len(intervals) == 1 and len(intervals[0]) == 1):
        for i in range(1, min(cardinal, intervals[0][0]) + 1):
            value_list.append(i)
    elif (len(intervals)):
        for interval in intervals:
            if (len(interval) == 1):
                if (interval[0] <= cardinal):
                    value_list.append(interval[0])
            elif (len(interval)):
                for i in range(interval[0], min(cardinal, interval[1]) + 1):
                    value_list.append(i)
    
    return value_list

def indefinite_article(word):
    article = "a"
    vowels = ['a', 'A', 'e', 'E', 'i', 'I', 'o', 'O', 'u', 'U']
    if (len(word) and word[0] in vowels):
        article = "an"
    return article

def get_default_emoji_url(emoji):
    emoji_id = ord(emoji[0])
    url = f"https://twemoji.maxcdn.com/v/latest/72x72/{emoji_id:x}.png"
    return url

def clean_thread_title(title, re):
    # Thread title = title with stripped patterns (re) < 100 characters
    if (title is None):
        title = ""
    if (len(title)):
        title = "".join(re.split(title)).strip()
    if (len(title) > 100): # discord refuses thread if title too long
        title = title[:100]
    return title

def get_id_from_mention(mention: str) -> int | None:
    match = re.match(r"^<(?:@!?|@&|#)([0-9]+)>$", mention)
    
    if match:
        return int(match.group(1))
    return None

def safe_list_get (l, idx, default):
  try:
    return l[idx]
  except IndexError:
    return default

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
