"""Constants for the matchmaking cog."""

import re

import discord

LFG_COMMAND = "lfg"
LFG_DESCRIPTION = "Looking for a game."

LFG_JOIN_BUTTON_LABEL = "Join/Leave"
LFG_NOTIFY_BUTTON_LABEL = "Toggle Notification"
LFG_CANCEL_BUTTON_LABEL = "Cancel"
LFG_START_BUTTON_LABEL = "Start"
LFG_JOIN_CUSTOM_ID = "lfg_view:join"
LFG_NOTIFY_CUSTOM_ID = "lfg_view:notify"
LFG_CANCEL_CUSTOM_ID = "lfg_view:cancel"
LFG_START_CUSTOM_ID = "lfg_view:start"

RENAME_COMMAND = "rename"
RENAME_DESCRIPTION = "Rename a game thread."

GAMES_COMMAND = "games"

# Sentinel guild id storing the [DEFAULT] section's games and parameters
# (Discord snowflakes are always positive, so 0 never collides).
# fallback for guilds without their own entry.
DEFAULT_GUILD_ID = 0

# Path to the games configuration (see games.ini): guild sections and their games.
GAMES_INI_PATH = 'config/games.ini'
# Path to the optional per-game parameter configuration (see games_parameters.ini).
GAMES_PARAMETERS_PATH = 'config/games_parameters.ini'

CONFIG_GAMES_COMMANDS = "GamesCommands"
CONFIG_GAMES_NAMES = "GamesFullNames"
CONFIG_GAMES_ROLES = "GamesRoles"
CONFIG_GAMES_ICONS = "GamesIcons"
CONFIG_GAMES_COLORS = "GamesColors"
CONFIG_GAMES_FORUMS = "GamesForums"
CONFIG_GAMES_TAGS = "GamesTags"
CONFIG_GAMES_VISIBILITY = "GamesVisibility"
CONFIG_GAMES_MESSAGES = "GamesMessages"
CONFIG_GAMES_REGISTRATION_API = "GamesRegistrationAPI"
CONFIG_GAMES_MATCH_API = "GamesMatchAPI"
CONFIG_GAMES_MATCH_URL = "GamesMatchURL"
CONFIG_GAMES_API_TOKEN_ENV_VARS = "GamesAPITokenEnvVars"
CONFIG_GAMES_WEBSITE_URL = "GamesWebsiteURL"
CONFIG_GAMES_REGISTRATION_URL = "GamesRegistrationURL"
CONFIG_GAMES_PROFILE_URL = "GamesProfileURL"
CONFIG_GAMES_MAX_PLAYERS = "GamesMaxPlayers"
CONFIG_GAMES_ARGS = [
    CONFIG_GAMES_COMMANDS,
    CONFIG_GAMES_NAMES,
    CONFIG_GAMES_ROLES,
    CONFIG_GAMES_ICONS,
    CONFIG_GAMES_COLORS,
    CONFIG_GAMES_FORUMS,
    CONFIG_GAMES_TAGS,
    CONFIG_GAMES_VISIBILITY,
    CONFIG_GAMES_MESSAGES,
    CONFIG_GAMES_REGISTRATION_API,
    CONFIG_GAMES_MATCH_API,
    CONFIG_GAMES_MATCH_URL,
    CONFIG_GAMES_API_TOKEN_ENV_VARS,
    CONFIG_GAMES_WEBSITE_URL,
    CONFIG_GAMES_REGISTRATION_URL,
    CONFIG_GAMES_PROFILE_URL,
    CONFIG_GAMES_MAX_PLAYERS
]
EMOJI_JOIN = "👍"
EMOJI_NOTIFY = "🔔"
EMOJI_CANCEL = "❌"
EMOJI_START = "✅"
EMOJIS_VALID = [EMOJI_JOIN, EMOJI_NOTIFY, EMOJI_CANCEL, EMOJI_START]
EMOJIS_CLOSE = [EMOJI_CANCEL, EMOJI_START]

LFG_FOOTER_HELP = "For discussion about this game, please use a thread.\nIt will be created for you when you close the game."

THREAD_TYPES = [discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
                discord.ChannelType.news_thread]

GUESTS_OVER_LIMIT = " and others..."

# Reserved keys in games_parameters.ini sections mapping the fixed match
# payload components (title, thread link, participants) to their API field
# names. Keys starting with API_FIELD_PREFIX are never treated as parameters.
API_FIELD_PREFIX = "api_"
API_TITLE_FIELD_KEY = API_FIELD_PREFIX + "title_field"
API_TABLE_TALK_URL_FIELD_KEY = API_FIELD_PREFIX + "table_talk_url_field"
API_PARTICIPANTS_FIELD_KEY = API_FIELD_PREFIX + "participants_field"
API_DISCORD_USERNAME_FIELD_KEY = API_FIELD_PREFIX + "discord_username_field"

# Compiled once at import time and reused everywhere: naming the pattern
# is self-documenting and avoids re-parsing it on each invocation.
LFG_TITLE_RE = re.compile(r"Looking for (?:an? )?(.+?) game$")
GUESTS_FIELD_RE = re.compile(r"Guests \((\d+)(?:/(\d+))?\)")