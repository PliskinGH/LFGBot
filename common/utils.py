# -*- coding: utf-8 -*-
"""
Created on Tue Mar 29 18:21:04 2022

@author: Pliskin
"""

from common.constants import (
    CONFIG_DEFAULT,
    CONFIG_ID,
    CUSTOM_EMOJI_RE,
    INTERVAL_RE,
    MENTION_RE,
)

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
    if not(INTERVAL_RE.match(string)):
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

def clean_thread_title(title, pattern=CUSTOM_EMOJI_RE):
    # Thread title = title with matched patterns stripped, capped at 100 chars
    if (title is None):
        title = ""
    if (len(title)):
        title = "".join(pattern.split(title)).strip()
    if (len(title) > 100): # discord refuses thread if title too long
        title = title[:100]
    return title

def get_id_from_mention(mention: str) -> int | None:
    match = MENTION_RE.fullmatch(mention)
    
    if match:
        return int(match.group(1))
    return None

def safe_list_get (l, idx, default):
  try:
    return l[idx]
  except IndexError:
    return default


def shorten(text: str, limit: int = 1024) -> str:
    """``text`` truncated to ``limit`` characters with an ellipsis.

    Keeps text within Discord's limits (an embed field value holds at most
    1024 characters, a field name 256, a plain message 2000); the
    truncation is marked so readers can tell the value was cut.
    """
    if (len(text) <= limit):
        return text
    return text[:limit - 1] + "…"


def clip_lines(lines: list[str], limit: int = 2000) -> str:
    """Join lines into a plain message clipped to ``limit`` characters.

    Whole lines are kept (a mid-line cut would look broken); when lines
    must be dropped the tail says how many were hidden. When a single line
    alone is too long, it is cut with an ellipsis instead.
    """
    if (not lines):
        return ""
    message = "\n".join(lines)
    if (len(message) <= limit):
        return message
    kept = []
    used = 0
    for line in lines:
        extra = (1 if kept else 0) + len(line)
        if (used + extra > limit):
            break
        kept.append(line)
        used += extra
    while (kept):
        remaining = len(lines) - len(kept)
        tail = "" if (not remaining) else f"\n… and {remaining} more not shown"
        if (len("\n".join(kept)) + len(tail) <= limit):
            return "\n".join(kept) + tail
        kept.pop()
    return shorten(lines[0], limit)

