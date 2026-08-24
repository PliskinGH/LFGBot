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

    async def setup_hook(self):
        """Runs automatically before the bot connects to Discord."""
        # Loop through files in the ./cogs directory
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                # Load extension using dot-notation: cogs.filename (without .py)
                await self.load_extension(f"cogs.{filename[:-3]}")
                print(f"Loaded cog: {filename[:-3]}")

        # Sync commands globally (can take up to 1 hour to propagate everywhere).
        # FOR QUICK TESTING: Sync to specific test guilds for instant updates.
        if (TEST_GUILD_IDS):
            for guild_id in TEST_GUILD_IDS:
                print(f"Syncing slash commands for test guild {guild_id}...")
                TEST_GUILD = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=TEST_GUILD)
                test_synced = await self.tree.sync(guild=TEST_GUILD)
                print(f"Synced {len(test_synced)} command(s) for test guild {guild_id}.")
        else:
            # Sync slash commands with Discord after all Cogs are loaded
            print("Syncing slash commands...")
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s) globally.")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")


bot = LFGBot()

if __name__ == "__main__":
    bot.run(TOKEN)