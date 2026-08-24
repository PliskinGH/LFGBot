import discord
from discord import app_commands
from discord.ext import commands

import random
import configparser
import json

from common import common, utils

RANDOM_COMMAND = "random"

class MatchRolls(commands.Cog):

    def __init__(self, bot: commands.Bot,
                 config: configparser.ConfigParser,
                 descriptions: dict):
        self.bot = bot
        self.descriptions = descriptions
        self.config = config

    async def send_help(self, interaction: discord.Interaction, topic: str):
        if (topic != RANDOM_COMMAND):
            message = (
                f"# Help: /{topic}\n"
                "No detailed help is available for this command yet."
            )
            await interaction.response.send_message(message, ephemeral=True)
            return

        guild = utils.get_guild_from_config(self.config, interaction.guild_id)
        roll_sets = []
        categories = []
        configured_sets = []
        if (guild == common.CONFIG_DEFAULT):
            configured_items = self.config.defaults().items()
        else:
            configured_items = self.config.items(guild)
        for category, roll_set in configured_items:
            if (not(category) or not(roll_set)):
                continue
            display_set = roll_set
            if (roll_set in configured_sets):
                index = configured_sets.index(roll_set)
                display_set = f"alias for `{categories[index]}`"
            categories.append(category)
            configured_sets.append(roll_set)
            roll_sets.append((category, display_set))

        if (categories):
            alignment = len(max(categories, key=len))
            roll_sets = [
                f"- `{category.ljust(alignment)}` - {display_set}.\n"
                for category, display_set in roll_sets
            ]

        message = (
            f"# Help: /{RANDOM_COMMAND}\n"
            "Choose a category and receive a random item from its set. "
            "Autocomplete shows the available options.\n"
            "## Usage\n"
            f"`/{RANDOM_COMMAND} category:<category> [subset:<subset>] "
            "[display:<true|false>]`\n"
            "## Subset examples\n"
            "- `subset:6` rolls from the first six items.\n"
            "- `subset:2,5-9` rolls from item 2 and items 5 through 9.\n"
            "## Display\n"
            "Set `display:false` for an ephemeral result. Results are "
            "public by default.\n"
            "## Available sets\n"
            f"{''.join(roll_sets) or 'No roll sets are configured.'}"
        )
        await interaction.response.send_message(message, ephemeral=True)

    # Autocomplete callback reads directly from self.available_choices
    async def category_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        
        guild_id = interaction.guild_id
        guild = utils.get_guild_from_config(self.config, guild_id)

        if (guild == common.CONFIG_DEFAULT):
            category_choices = list(self.config.defaults().keys())
        else:
            category_choices = self.config.options(guild)

        return [
            app_commands.Choice(name=choice, value=choice)
            for choice in category_choices
            if current.lower() in choice.lower()
        ][
            :25
        ]  # Discord allows max 25 returned options

    # random
    @app_commands.command(
        name=RANDOM_COMMAND, description="Random roll from a set."
    )
    @app_commands.describe(category="Category of the set to roll from.", subset="Specific subset to roll from.")
    @app_commands.autocomplete(category=category_autocomplete)
    async def random(self, 
                     interaction: discord.Interaction,
                     category: str,
                     subset: str | None = None,
                     display: bool = True):
        guild_id = interaction.guild_id
        guild = utils.get_guild_from_config(self.config, guild_id)

        choices = utils.split_config_list(self.config.get(guild, category, fallback=None))
        cardinal = len(choices)
        subset_choices = choices
        if (subset is not None):
            value_list = utils.parse_intervals(subset, cardinal)
            subset_choices = [choices[i-1] for i in value_list]

        choice = ""
        if (len(subset_choices)):
            choice = random.choice(subset_choices)
            footer_text = "Randomly chosen among: " + ", ".join(subset_choices) + "."
        
        nb_descriptions = 0
        if (len(choice)):
            dicts = [ _dict for _dict in self.descriptions
                      if ("title" in _dict and _dict["title"] == choice) ]
            nb_descriptions = len(dicts)
        description = {}
        if (nb_descriptions >= 1):
            description = dicts[random.randrange(0, nb_descriptions)]
        
        if (len(description)):
            embed = discord.Embed.from_dict(description)
        
            if ("color" not in description):
                embed.colour = discord.Colour.random()
            
            if ("title" in description):
                if ("category" in description):
                    embed.title = "Random " + description["category"] + ": " + description["title"]
                author_avatar = common.DEFAULT_AVATAR_URL
                display_avatar = interaction.user.display_avatar
                if (display_avatar is not None):
                    author_avatar = display_avatar.url
                embed.set_author(name=interaction.user.display_name,
                                 icon_url=author_avatar)
                embed.set_footer(text=footer_text)
                await interaction.response.send_message(embed=embed, ephemeral=not(display))
                return

        await interaction.response.send_message(f"No item found in the set or subset for `{category}`.", ephemeral=True)

# Mandatory setup function for extensions
async def setup(bot: commands.Bot):
    config = configparser.ConfigParser()
    config.read('config/rolls.ini')
    
    json_file = open('config/rolls_descriptions.json', encoding="utf8")
    descriptions = json.load(json_file)
    json_file.close()

    await bot.add_cog(MatchRolls(bot, config, descriptions))