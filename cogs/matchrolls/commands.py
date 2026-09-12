"""The /random slash command of the matchrolls cog."""

import random

import discord
from discord import app_commands

from common import constants as common_constants, utils

from . import constants


class RollsCommandsMixin:
    """The /random command and its category autocomplete."""

    async def category_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        roll_sets = self.get_roll_sets(interaction.guild_id)
        return [
            app_commands.Choice(name=choice, value=choice)
            for choice in roll_sets
            if current.lower() in choice.lower()
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    # random
    @app_commands.command(
        name=constants.RANDOM_COMMAND, description="Random roll from a set."
    )
    @app_commands.describe(category="Category of the set to roll from.", subset="Specific subset to roll from.")
    @app_commands.autocomplete(category=category_autocomplete)
    async def random(self,
                     interaction: discord.Interaction,
                     category: str,
                     subset: str | None = None,
                     display: bool = True):
        roll_sets = self.get_roll_sets(interaction.guild_id)

        choices = utils.split_config_list(roll_sets.get(category))
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
            dicts = [ _dict for _dict in self.get_descriptions(interaction.guild_id)
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
                author_avatar = common_constants.DEFAULT_AVATAR_URL
                display_avatar = interaction.user.display_avatar
                if (display_avatar is not None):
                    author_avatar = display_avatar.url
                embed.set_author(name=interaction.user.display_name,
                                 icon_url=author_avatar)
                embed.set_footer(text=footer_text)
                await interaction.response.send_message(embed=embed, ephemeral=not(display))
                return

        await interaction.response.send_message(f"No item found in the set or subset for `{category}`.", ephemeral=True)
