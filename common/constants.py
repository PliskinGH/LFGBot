import re

CONFIG_DEFAULT = "DEFAULT"
CONFIG_ID = "ID"

HELP_COMMAND = "help"

DEFAULT_AVATAR_URL = "https://i.imgur.com/xClQZ1Q.png"

# Discord returns at most 25 choices for an autocomplete interaction, so every
# autocomplete callback must truncate its result list to this.
AUTOCOMPLETE_LIMIT = 25

# Discord caps a message's plain content at 2000 characters and an embed's
# description at 4096, so anything longer must be truncated to stay valid.
MESSAGE_CONTENT_LIMIT = 2000
EMBED_DESCRIPTION_LIMIT = 4096

# Compiled once at import time and reused everywhere: naming the pattern
# is self-documenting and avoids re-parsing it on each invocation.
INTERVAL_RE = re.compile(r"^[0-9\-\,]*$")
MENTION_RE = re.compile(r"<(?:@!?|@&|#)(\d+)>")
CUSTOM_EMOJI_RE = re.compile(r"<:[\w]+:[\d]+>")