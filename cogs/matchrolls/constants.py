"""Constants for the matchrolls cog."""

RANDOM_COMMAND = "random"

# Sentinel guild id storing the [DEFAULT] section's roll categories
# (Discord snowflakes are always positive, so 0 never collides).
DEFAULT_GUILD_ID = 0

# Path to the rolls configuration (see rolls.ini).
ROLLS_INI_PATH = 'config/rolls.ini'
# Path to the JSON file holding the roll descriptions.
ROLLS_DESCRIPTIONS_PATH = 'config/rolls_descriptions.json'
