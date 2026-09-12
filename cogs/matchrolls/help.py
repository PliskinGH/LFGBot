"""Help text for the matchrolls cog's /random command."""

import discord

from . import constants


class RollsHelpMixin:
    """``send_help`` for the /help command dispatch."""

    async def send_help(self, interaction: discord.Interaction, topic: str):
        if (topic == constants.ROLLSETS_COMMAND):
            message = (
                f"# Help: /{constants.ROLLSETS_COMMAND}\n"
                "Manage this server's roll sets: the categories of `/random`, "
                "their items, and each item's description variants.\n"
                "Only available to server managers, in database mode.\n"
                "## Categories\n"
                f"`/{constants.ROLLSETS_COMMAND} list` — the categories "
                "and their items.\n"
                f"`/{constants.ROLLSETS_COMMAND} show category:<category>` — "
                "the items of a category (removing an item from its set "
                "keeps its descriptions; re-adding restores them).\n"
                f"`/{constants.ROLLSETS_COMMAND} add "
                "category:<category> items:<list>`\n"
                f"`/{constants.ROLLSETS_COMMAND} update "
                "category:<category> [items:<list>] [new_name:<name>]`\n"
                f"`/{constants.ROLLSETS_COMMAND} remove category:<category>`\n"
                "## Descriptions\n"
                f"`/{constants.ROLLSETS_COMMAND} description list "
                "[category:<category>]` — each item and its variant count.\n"
                f"`/{constants.ROLLSETS_COMMAND} description show "
                "item:<item> [variant:<n>]` — an item's descriptions.\n"
                f"`/{constants.ROLLSETS_COMMAND} description add "
                "item:<item> text:<text> [color:<n>] "
                "[image:<url>] [thumbnail:<url>]`\n"
                f"`/{constants.ROLLSETS_COMMAND} description update "
                "item:<item> variant:<n> [text:<text>] [color:<n|->] "
                "[image:<url|->] [thumbnail:<url|->]`\n"
                f"`/{constants.ROLLSETS_COMMAND} description remove "
                "item:<item> variant:<n>`\n"
            )
            await interaction.response.send_message(message, ephemeral=True)
            return

        if (topic != constants.RANDOM_COMMAND):
            message = (
                f"# Help: /{topic}\n"
                "No detailed help is available for this command yet."
            )
            await interaction.response.send_message(message, ephemeral=True)
            return

        configured_items = self.get_roll_sets(interaction.guild_id).items()
        roll_sets = []
        categories = []
        configured_sets = []
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
            f"# Help: /{constants.RANDOM_COMMAND}\n"
            "Choose a category and receive a random item from its set. "
            "Autocomplete shows the available options.\n"
            "## Usage\n"
            f"`/{constants.RANDOM_COMMAND} category:<category> [subset:<subset>] "
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
