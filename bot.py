import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

from common import utils

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('COMMAND_PREFIX')
# TEST_GUILD_ID accepts a comma-separated list of guild IDs to sync slash
# commands to (e.g. "123,456,789") for quick testing across several
# servers, instead of waiting for global command propagation.
TEST_GUILD_IDS = utils.split_config_list(os.getenv('TEST_GUILD_ID'))
if (PREFIX is None):
    PREFIX = "!"

# 1. Define Intents and Allowed mentions
intents = discord.Intents.default()
intents.members = True
allowed_mentions = discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=True)

# 2. Subclass Bot
class LFGBot(commands.Bot):

    def __init__(self):
        super().__init__(command_prefix=PREFIX, intents=intents, allowed_mentions=allowed_mentions)
        # Guild ids that cogs registered per-guild slash commands for. Populated
        # while extensions are loaded and consumed in setup_hook to sync those
        # guilds. Only relevant outside TEST_GUILD_ID mode.
        self.provided_guild_ids: set[int] = set()

    async def setup_hook(self):
        """Runs automatically before the bot connects to Discord."""
        # Loop through files in the ./cogs directory
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                # Load extension using dot-notation: cogs.filename (without .py)
                await self.load_extension(f"cogs.{filename[:-3]}")
                print(f"Loaded cog: {filename[:-3]}")
            elif (os.path.isdir(os.path.join("./cogs", filename))
                  and os.path.exists(os.path.join("./cogs", filename, "__init__.py"))
                  and not os.path.exists(os.path.join("./cogs", filename + ".py"))):
                # Cog package (e.g. cogs/matchmaking/). Loaded only when the
                # single-file version is absent, so both can coexist during a
                # migration without loading the same extension twice.
                await self.load_extension(f"cogs.{filename}")
                print(f"Loaded cog package: {filename}")

        # FOR QUICK TESTING: Sync to specific test guilds for instant updates.
        if (TEST_GUILD_IDS):
            for guild_id in TEST_GUILD_IDS:
                print(f"Syncing slash commands for test guild {guild_id}...")
                TEST_GUILD = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=TEST_GUILD)
                try:
                    test_synced = await self.tree.sync(guild=TEST_GUILD)
                    print(f"Synced {len(test_synced)} command(s) for test guild {guild_id}.")
                except discord.HTTPException as error:
                    # e.g. the bot is no longer on that guild (Missing Access)
                    # or the guild no longer exists. Log and continue so one
                    # stale guild does not prevent the bot from starting.
                    print(f"Failed to sync commands for test guild {guild_id}: {error}")
        else:
            # Cog setup() may have registered guild-specific slash commands and
            # recorded the target guilds in self.provided_guild_ids. Sync each of
            # those guilds, then sync globally so guilds that are not listed in
            # the games config can still use the global /lfg command.
            for guild_id in sorted(self.provided_guild_ids):
                print(f"Syncing per-guild commands for guild {guild_id}...")
                guild = discord.Object(id=guild_id)
                try:
                    synced = await self.tree.sync(guild=guild)
                    print(f"Synced {len(synced)} command(s) for guild {guild_id}.")
                except discord.HTTPException as error:
                    # e.g. the bot is no longer on that guild (Missing Access)
                    # or the guild no longer exists. Log and continue so one
                    # stale guild does not prevent the bot from starting.
                    print(f"Failed to sync commands for guild {guild_id}: {error}")
            # Sync global slash commands
            print("Syncing slash commands...")
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s) globally.")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")


bot = LFGBot()

if __name__ == "__main__":
    bot.run(TOKEN)