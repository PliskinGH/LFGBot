"""Admin slash commands: manage a server's roll sets (database mode)."""

import discord
from discord import app_commands

from common import constants as common_constants, utils as common_utils
from common.views import ConfirmView

from . import constants, db_config


class RollsAdminMixin:
    """/rollsets: the server's roll categories, their items, and the items'
    description variants.

    These write to the database (the runtime source of truth) and refresh the
    cog's in-memory configuration. They require the ``manage_guild``
    permission and only work when the bot runs in database mode
    (``DATABASE_URL`` set); config-file mode is read-only.
    """

    rollsets = app_commands.Group(
        name=constants.ROLLSETS_COMMAND,
        description="Manage this server's roll sets.",
        # Hide the whole /rollsets group (including its subcommands) from
        # members without the manage_guild permission.
        default_permissions=discord.Permissions(manage_guild=True))

    # Nested subgroup (/rollsets description ...), attached to the group at
    # the end of this class so CogMeta registers it as a child command.
    rollsets_description = app_commands.Group(
        name="description", description="Manage an item's description variants.")

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _category_name_error(value: str, fallback: str) -> str | None:
        """An error message when a category name is invalid, else None."""
        name = value.strip()
        if (not name or len(name) > 50 or "\n" in name or "\r" in name):
            return f"`{fallback}` must be 1-50 characters without newlines."
        if (name == common_constants.CONFIG_ID.lower()):
            return (f"`{fallback}` cannot be "
                    f"`{common_constants.CONFIG_ID.lower()}` (reserved).")
        return None

    @staticmethod
    def _item_names_error(value: str) -> tuple[list[str] | None, str | None]:
        """(names, error): the parsed item names, or an error message."""
        names = [item.strip() for item in value.split(",")]
        if (not names or not names[0]):
            return None, "`items` requires at least one item."
        for name in names:
            if (not name or len(name) > 50 or "\n" in name or "\r" in name):
                return None, ("`items` must be a comma-separated list of "
                              "1-50 character names.")
        if (len(set(names)) != len(names)):
            return None, "`items` cannot contain duplicate names."
        return names, None

    @staticmethod
    def _parse_color(value: str | None) -> tuple[int | None, str | None]:
        """(color, error): ``-`` clears the color; None keeps it."""
        if (value is None or value == common_constants.RESET_SENTINEL):
            return None, None
        try:
            color = int(value)
        except ValueError:
            return None, "`color` must be an integer 0-16777215, or `-` to clear it."
        if (not (0 <= color <= 0xFFFFFF)):
            return None, "`color` must be an integer 0-16777215, or `-` to clear it."
        return color, None

    @staticmethod
    def _parse_url(value: str | None, name: str) -> tuple[str | None, str | None]:
        """(url, error): ``-`` clears the value; None keeps it."""
        if (value is None or value == common_constants.RESET_SENTINEL):
            return None, None
        value = value.strip()
        if (not value or len(value) > 2048):
            return None, (f"`{name}` must be a URL of at most 2048 characters, "
                          "or `-` to clear it.")
        return value, None

    @staticmethod
    def _parse_text(value: str | None) -> tuple[str | None, str | None]:
        """(text, error): ``-`` clears the text; None keeps it."""
        if (value is None):
            return None, None
        if (value == common_constants.RESET_SENTINEL):
            return "", None
        if (not value or len(value) > common_constants.EMBED_DESCRIPTION_LIMIT):
            return None, (f"`text` must be 1-{common_constants.EMBED_DESCRIPTION_LIMIT} "
                          "characters, or `-` to clear it.")
        return value, None

    async def _category_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        categories = list(await db_config.effective_category_sets(
            interaction.guild_id))
        return [
            app_commands.Choice(name=name, value=name)
            for name in categories
            if current.lower() in name.lower()
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    async def _item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if (interaction.guild_id is None):
            return []
        items = await db_config.active_item_names(interaction.guild_id)
        return [
            app_commands.Choice(name=name, value=name)
            for name in items
            if current.lower() in name.lower()
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    # ------------------------------------------------------------------ #
    # Categories
    # ------------------------------------------------------------------ #

    @rollsets.command(name="list", description="List this server's roll categories and their items.")
    async def rollsets_list(self, interaction: discord.Interaction):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        await db_config.ensure_guild_categories(guild_id)
        lines = [
            f"- `{name}` — {roll_set}"
            for name, roll_set in (await db_config.effective_category_sets(
                guild_id)).items()
        ]
        message = "# Roll sets\n" + common_utils.clip_lines(lines)
        if (not lines):
            message = "# Roll sets\nNo roll categories are configured."
        await interaction.response.send_message(message, ephemeral=True)

    @rollsets.command(name="show", description="Show all the items of a category.")
    @app_commands.describe(category="The category to show.")
    @app_commands.autocomplete(category=_category_autocomplete)
    async def rollsets_show(self, interaction: discord.Interaction,
                            category: str):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        await db_config.ensure_guild_categories(guild_id)
        active, inactive = await db_config.list_category_items(guild_id, category)
        if (not active and not inactive):
            await interaction.response.send_message(
                f"There is no roll category `{category}`.", ephemeral=True)
            return
        lines = [f"{index}. {name}" for index, name in enumerate(active, 1)]
        if (inactive):
            lines.append("")
            lines.append("Removed from the set (re-adding a name restores "
                         "its descriptions and its items here): "
                         + ", ".join(inactive) + ".")
        await interaction.response.send_message(
            f"# Category: `{category}`\n" + common_utils.clip_lines(lines),
            ephemeral=True)

    @rollsets.command(name="add", description="Add a roll category.")
    @app_commands.describe(category="The category name.", items="Its items, comma-separated.")
    async def rollsets_add(self, interaction: discord.Interaction,
                           category: str, items: str):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        if (error := self._category_name_error(category, "category")):
            await interaction.response.send_message(error, ephemeral=True)
            return
        item_names, error = self._item_names_error(items)
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        category = category.strip()
        if (not await db_config.add_category(guild_id, category, item_names)):
            await interaction.response.send_message(
                f"A roll category `{category}` already exists here.",
                ephemeral=True)
            return
        await self._reload()
        await interaction.response.send_message(
            f"Roll category `{category}` added "
            f"({len(item_names)} items).",
            ephemeral=True)

    @rollsets.command(name="update", description="Rename a roll category or replace its items.")
    @app_commands.describe(category="The category to update.",
                           items="Its new items, comma-separated (optional).",
                           new_name="A new name for the category (optional).")
    @app_commands.autocomplete(category=_category_autocomplete)
    async def rollsets_update(self, interaction: discord.Interaction,
                              category: str, items: str | None = None,
                              new_name: str | None = None):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        if (error := self._category_name_error(category, "category")):
            await interaction.response.send_message(error, ephemeral=True)
            return
        if (new_name is not None and (error := self._category_name_error(
                new_name, "new_name"))):
            await interaction.response.send_message(error, ephemeral=True)
            return
        item_names = None
        if (items is not None):
            item_names, error = self._item_names_error(items)
            if (error):
                await interaction.response.send_message(error, ephemeral=True)
                return
        category = category.strip()
        if (new_name is not None):
            new_name = new_name.strip()
            sets = await db_config.effective_category_sets(guild_id)
            if (new_name in sets and new_name != category):
                await interaction.response.send_message(
                    f"`{new_name}` is already a roll category here.",
                    ephemeral=True)
                return
        if (not await db_config.update_category(
                guild_id, category, item_names, new_name)):
            await interaction.response.send_message(
                f"There is no roll category `{category}` in this server.",
                ephemeral=True)
            return
        await self._reload()
        if (new_name and new_name != category):
            message = f"Roll category `{category}` renamed to `{new_name}`."
        else:
            message = f"Roll category `{category}` updated "
            if (item_names is not None):
                message += f"({len(item_names)} items)."
        await interaction.response.send_message(message, ephemeral=True)

    @rollsets.command(name="remove", description="Delete a roll category (asks for confirmation).")
    @app_commands.describe(category="The category to delete.")
    @app_commands.autocomplete(category=_category_autocomplete)
    async def rollsets_remove(self, interaction: discord.Interaction,
                              category: str):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        await db_config.ensure_guild_categories(guild_id)
        active, inactive = await db_config.list_category_items(guild_id, category)
        if (not active and not inactive):
            await interaction.response.send_message(
                f"There is no roll category `{category}`.", ephemeral=True)
            return
        variants = sum(count for _, count, _ in
                       await db_config.item_variant_counts(guild_id, category))
        item_count = len(active) + len(inactive)

        async def confirm() -> str:
            if (await db_config.delete_category(guild_id, category)):
                await self._reload()
                return f"Deleted roll category `{category}`."
            return f"There is no roll category `{category}`."

        async def cancel() -> str:
            return "Cancelled."

        view = ConfirmView(interaction.user.id, "Delete category",
                           confirm, cancel)
        await interaction.response.send_message(
            f"# Delete roll category `{category}`\n"
            f"This will permanently delete the category and its "
            f"**{item_count} item(s)** and **{variants} description "
            "variant(s)**. This cannot be undone.",
            view=view, ephemeral=True)

    # ------------------------------------------------------------------ #
    # Descriptions
    # ------------------------------------------------------------------ #

    @rollsets_description.command(
        name="list", description="List each item and its variant count.")
    @app_commands.describe(
        category="Restrict the list to one category (optional).")
    @app_commands.autocomplete(category=_category_autocomplete)
    async def description_list(self, interaction: discord.Interaction,
                               category: str | None = None):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        if (category is not None):
            if (error := self._category_name_error(category, "category")):
                await interaction.response.send_message(error, ephemeral=True)
                return
            category = category.strip()
        rows = await db_config.item_variant_counts(guild_id, category)
        lines = [f"- `{name}` — {count} variant(s)."
                 for name, count, active in rows]
        if (not lines):
            await interaction.response.send_message(
                "There are no items here yet.", ephemeral=True)
            return
        await interaction.response.send_message(
            "# Description variants\n" + common_utils.clip_lines(lines),
            ephemeral=True)

    @rollsets_description.command(
        name="show", description="Show an item's description variants.")
    @app_commands.describe(item="The item.", variant="The variant number (optional).")
    @app_commands.autocomplete(item=_item_autocomplete)
    async def description_show(self, interaction: discord.Interaction,
                               item: str, variant: int | None = None):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        embeds = await db_config.description_variant_embeds(guild_id, item)
        if (variant is not None):
            if (variant < 1 or variant > len(embeds)):
                await interaction.response.send_message(
                    f"`{item}` has no variant #{variant}.", ephemeral=True)
                return
            embeds = [embeds[variant - 1]]
        if (not embeds):
            await interaction.response.send_message(
                f"`{item}` has no description variants.", ephemeral=True)
            return
        await interaction.response.send_message(
            embeds=[discord.Embed.from_dict(embed) for embed in embeds],
            ephemeral=True)

    @rollsets_description.command(
        name="add", description="Add a description variant to an item.")
    @app_commands.describe(item="The item.",
                           text="The variant text.",
                           color="Its colour as an integer (optional).",
                           image="The image URL (optional).",
                           thumbnail="The thumbnail URL (optional).")
    @app_commands.autocomplete(item=_item_autocomplete)
    async def description_add(self, interaction: discord.Interaction,
                              item: str, text: str, color: str | None = None,
                              image: str | None = None,
                              thumbnail: str | None = None):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        text_value, error = self._parse_text(text)
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        color_value, error = self._parse_color(color)
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        image_url, error = self._parse_url(image, "image")
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        thumbnail_url, error = self._parse_url(thumbnail, "thumbnail")
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        fields = {"description": text_value}
        if (color_value is not None):
            fields["color"] = color_value
        if (image_url is not None):
            fields["image_url"] = image_url
        if (thumbnail_url is not None):
            fields["thumbnail_url"] = thumbnail_url
        if (not await db_config.add_description(guild_id, item, fields)):
            await interaction.response.send_message(
                f"There is no active item `{item}` in this server.",
                ephemeral=True)
            return
        await self._reload()
        await interaction.response.send_message(
            f"Added a description variant to `{item}`.", ephemeral=True)

    @rollsets_description.command(
        name="update", description="Update one description variant of an item.")
    @app_commands.describe(item="The item.", variant="The variant number.",
                           text="The new text, or `-` to clear it (optional).",
                           color="Its colour, or `-` to clear it (optional).",
                           image="The image URL, or `-` to clear it (optional).",
                           thumbnail="The thumbnail URL, or `-` to clear it (optional).")
    @app_commands.autocomplete(item=_item_autocomplete)
    async def description_update(self, interaction: discord.Interaction,
                                 item: str, variant: int, text: str | None = None,
                                 color: str | None = None,
                                 image: str | None = None,
                                 thumbnail: str | None = None):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        if (variant < 1):
            await interaction.response.send_message(
                "`variant` must be a positive index.", ephemeral=True)
            return
        fields: dict = {}
        if (text is not None):
            text_value, error = self._parse_text(text)
            if (error):
                await interaction.response.send_message(error, ephemeral=True)
                return
            fields["description"] = text_value
        if (color is not None):
            color_value, error = self._parse_color(color)
            if (error):
                await interaction.response.send_message(error, ephemeral=True)
                return
            fields["color"] = color_value
        if (image is not None):
            image_url, error = self._parse_url(image, "image")
            if (error):
                await interaction.response.send_message(error, ephemeral=True)
                return
            fields["image_url"] = image_url
        if (thumbnail is not None):
            thumbnail_url, error = self._parse_url(thumbnail, "thumbnail")
            if (error):
                await interaction.response.send_message(error, ephemeral=True)
                return
            fields["thumbnail_url"] = thumbnail_url
        if (not await db_config.update_description(
                guild_id, item, variant, fields)):
            await interaction.response.send_message(
                f"`{item}` has no variant #{variant}.", ephemeral=True)
            return
        await self._reload()
        await interaction.response.send_message(
            f"Updated variant #{variant} of `{item}`.", ephemeral=True)

    @rollsets_description.command(
        name="remove", description="Remove one description variant of an item.")
    @app_commands.describe(item="The item.", variant="The variant number.")
    @app_commands.autocomplete(item=_item_autocomplete)
    async def description_remove(self, interaction: discord.Interaction,
                                 item: str, variant: int):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        if (variant < 1):
            await interaction.response.send_message(
                "`variant` must be a positive index.", ephemeral=True)
            return
        if (not await db_config.delete_description(guild_id, item, variant)):
            await interaction.response.send_message(
                f"`{item}` has no variant #{variant}.", ephemeral=True)
            return
        await self._reload()
        await interaction.response.send_message(
            f"Removed variant #{variant} from `{item}`.", ephemeral=True)

    # Attach the description subgroup to the rollsets group now that its
    # subcommands exist; CogMeta skips it as a top-level command (parent set).
    rollsets.add_command(rollsets_description)

    async def _guard_admin(self, interaction: discord.Interaction) -> bool:
        """Reject non-managers with an ephemeral message."""
        permissions = getattr(interaction.user, "guild_permissions", None)
        if (permissions is None or not permissions.manage_guild):
            await interaction.response.send_message(
                "Only server managers can change the roll configuration.",
                ephemeral=True)
            return False
        return True

    async def _guard_database(self, interaction: discord.Interaction) -> bool:
        """Reject config-file mode (there is no database to write to)."""
        if (getattr(self.bot, "db", None) is None):
            await interaction.response.send_message(
                "This bot runs in config-file mode and cannot be reconfigured "
                "here; set DATABASE_URL to enable dynamic configuration.",
                ephemeral=True)
            return False
        return True

    async def _reload(self) -> None:
        """Refresh the in-memory configuration from the database."""
        loaded = await db_config.load_config_from_db()
        self.default_categories = loaded.default_categories
        self.guilds = loaded.guilds
        self.default_descriptions = loaded.default_descriptions
        self.guild_descriptions = loaded.guild_descriptions

    async def _expect_guild(self, interaction: discord.Interaction) -> int | None:
        """Send an ephemeral error and return None outside a guild."""
        if (interaction.guild_id is None):
            await interaction.response.send_message(
                "This command is only available in a server.", ephemeral=True)
            return None
        return interaction.guild_id