"""Admin slash commands: manage a server's roll sets (database mode)."""

import discord
from discord import app_commands

from common import constants as common_constants, utils as common_utils
from common.views import ConfirmView
from db import config_log

from . import constants, db_config, validation


class RollsAdminMixin:
    """/rollsets: the server's roll categories, their items, and the items'
    description variants.

    These write to the database (the runtime source of truth) and refresh the
    cog's in-memory configuration. They require the ``manage_guild``
    permission and only work when the bot runs in database mode
    (``DATABASE_URL`` set); config-file mode is read-only.
    """

    # The queued change actions this cog owns (see db/config_queue.py).
    change_prefixes = ("rollset.",)

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

    # Nested subgroup (/rollsets item ...), attached the same way.
    rollsets_item = app_commands.Group(
        name="item", description="Manage one item of a category.")

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
        items = await db_config.active_items(interaction.guild_id)
        return [
            app_commands.Choice(name=db_config.item_label(item),
                                value=str(item.id))
            for item in items
            if (current.lower() in db_config.item_label(item).lower()
                or current.lower() in item.name.lower())
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    async def _item_of(self, guild_id: int, item: str):
        """The item an option names, by its row id or the label it showed.

        The guild's own rows come first: an id can only address a row the guild
        can see, and materializing copies [DEFAULT]'s items under new ids.
        """
        await db_config.ensure_guild_categories(guild_id)
        return await db_config.item_for_option(guild_id, item)

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
        await interaction.response.defer(ephemeral=True)
        lines = [
            f"- `{name}` — {roll_set}"
            for name, roll_set in (await db_config.effective_category_sets(
                guild_id)).items()
        ]
        message = "# Roll sets\n" + common_utils.clip_lines(lines)
        if (not lines):
            message = "# Roll sets\nNo roll categories are configured."
        await interaction.followup.send(message, ephemeral=True)

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
        await interaction.response.defer(ephemeral=True)
        active, inactive = await db_config.list_category_items(guild_id, category)
        if (not active and not inactive):
            await interaction.followup.send(
                f"There is no roll category `{category}`.", ephemeral=True)
            return
        lines = [f"{index}. {name}" for index, name in enumerate(active, 1)]
        if (inactive):
            lines.append("")
            lines.append("Removed from the set (re-adding a name restores "
                         "its descriptions and its items here): "
                         + ", ".join(inactive) + ".")
        await interaction.followup.send(
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
        if (error := validation.category_name_error(category, "category")):
            await interaction.response.send_message(error, ephemeral=True)
            return
        item_names, error = validation.item_names_error(items)
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        category = category.strip()
        await interaction.response.defer(ephemeral=True)
        if (not await db_config.add_category(guild_id, category, item_names)):
            await interaction.followup.send(
                f"A roll category `{category}` already exists here.",
                ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "rollset.category.add", category)
        await self.reload_config()
        await interaction.followup.send(
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
        if (error := validation.category_name_error(category, "category")):
            await interaction.response.send_message(error, ephemeral=True)
            return
        if (new_name is not None and (error := validation.category_name_error(
                new_name, "new_name"))):
            await interaction.response.send_message(error, ephemeral=True)
            return
        item_names = None
        if (items is not None):
            item_names, error = validation.item_names_error(items)
            if (error):
                await interaction.response.send_message(error, ephemeral=True)
                return
        category = category.strip()
        await interaction.response.defer(ephemeral=True)
        if (new_name is not None):
            new_name = new_name.strip()
            sets = await db_config.effective_category_sets(guild_id)
            if (new_name in sets and new_name != category):
                await interaction.followup.send(
                    f"`{new_name}` is already a roll category here.",
                    ephemeral=True)
                return
        if (not await db_config.update_category(
                guild_id, category, item_names, new_name)):
            await interaction.followup.send(
                f"There is no roll category `{category}` in this server.",
                ephemeral=True)
            return
        summary = category
        if (new_name and new_name != category):
            summary = f"{category} -> {new_name}"
        await config_log.record_command_change(
            interaction, "rollset.category.update", summary)
        await self.reload_config()
        if (new_name and new_name != category):
            message = f"Roll category `{category}` renamed to `{new_name}`."
        else:
            message = f"Roll category `{category}` updated "
            if (item_names is not None):
                message += f"({len(item_names)} items)."
        await interaction.followup.send(message, ephemeral=True)

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
        await interaction.response.defer(ephemeral=True)
        active, inactive = await db_config.list_category_items(guild_id, category)
        if (not active and not inactive):
            await interaction.followup.send(
                f"There is no roll category `{category}`.", ephemeral=True)
            return
        variants = sum(count for _, _, count, _ in
                       await db_config.item_variant_counts(guild_id, category))
        item_count = len(active) + len(inactive)

        async def confirm() -> str:
            if (await db_config.delete_category(guild_id, category)):
                await config_log.record_command_change(
                    interaction, "rollset.category.remove", category)
                await self.reload_config()
                return f"Deleted roll category `{category}`."
            return f"There is no roll category `{category}`."

        async def cancel() -> str:
            return "Cancelled."

        view = ConfirmView(interaction.user.id, "Delete category",
                           confirm, cancel)
        await interaction.followup.send(
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
            if (error := validation.category_name_error(category, "category")):
                await interaction.response.send_message(error, ephemeral=True)
                return
            category = category.strip()
        await interaction.response.defer(ephemeral=True)
        rows = await db_config.item_variant_counts(guild_id, category)
        lines = [f"- `{name}` — {count} variant(s)."
                 for _item_id, name, count, _active in rows]
        if (not lines):
            await interaction.followup.send(
                "There are no items here yet.", ephemeral=True)
            return
        await interaction.followup.send(
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
        await interaction.response.defer(ephemeral=True)
        resolved = await db_config.item_for_option(guild_id, item)
        if (resolved is None):
            await interaction.followup.send(
                f"There is no item `{item}` in this server.", ephemeral=True)
            return
        label = db_config.item_label(resolved)
        embeds = await db_config.description_variant_embeds(guild_id,
                                                            resolved.id)
        if (variant is not None):
            if (variant < 1 or variant > len(embeds)):
                await interaction.followup.send(
                    f"`{label}` has no variant #{variant}.", ephemeral=True)
                return
            embeds = [embeds[variant - 1]]
        if (not embeds):
            await interaction.followup.send(
                f"`{label}` has no description variants.", ephemeral=True)
            return
        await interaction.followup.send(
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
        fields, error = validation.variant_fields(text, color, image, thumbnail)
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        resolved = await self._item_of(guild_id, item)
        if (resolved is None):
            await interaction.followup.send(
                f"There is no active item `{item}` in this server.",
                ephemeral=True)
            return
        if (not await db_config.add_description(guild_id, resolved.id, fields)):
            await interaction.followup.send(
                f"There is no active item `{item}` in this server.",
                ephemeral=True)
            return
        label = db_config.item_label(resolved)
        await config_log.record_command_change(
            interaction, "rollset.description.add", label)
        await self.reload_config()
        await interaction.followup.send(
            f"Added a description variant to `{label}`.", ephemeral=True)

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
        fields, error = validation.variant_fields(text, color, image, thumbnail)
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        resolved = await self._item_of(guild_id, item)
        if (resolved is None):
            await interaction.followup.send(
                f"There is no item `{item}` in this server.", ephemeral=True)
            return
        label = db_config.item_label(resolved)
        if (not await db_config.update_description(
                guild_id, resolved.id, variant, fields)):
            await interaction.followup.send(
                f"`{label}` has no variant #{variant}.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "rollset.description.update", f"{label} #{variant}")
        await self.reload_config()
        await interaction.followup.send(
            f"Updated variant #{variant} of `{label}`.", ephemeral=True)

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
        await interaction.response.defer(ephemeral=True)
        resolved = await self._item_of(guild_id, item)
        if (resolved is None):
            await interaction.followup.send(
                f"There is no item `{item}` in this server.", ephemeral=True)
            return
        label = db_config.item_label(resolved)
        if (not await db_config.delete_description(guild_id, resolved.id,
                                                   variant)):
            await interaction.followup.send(
                f"`{label}` has no variant #{variant}.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "rollset.description.remove", f"{label} #{variant}")
        await self.reload_config()
        await interaction.followup.send(
            f"Removed variant #{variant} from `{label}`.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # Items
    # ------------------------------------------------------------------ #

    @rollsets_item.command(
        name="add", description="Add an item to a category, with its first description.")
    @app_commands.describe(category="The category to add the item to.",
                           item="The item's name.",
                           text="Its first variant's text, or `-` for none.",
                           color="Its colour as an integer (optional).",
                           image="The image URL (optional).",
                           thumbnail="The thumbnail URL (optional).")
    @app_commands.autocomplete(category=_category_autocomplete)
    async def item_add(self, interaction: discord.Interaction, category: str,
                       item: str, text: str, color: str | None = None,
                       image: str | None = None, thumbnail: str | None = None):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        if (error := validation.category_name_error(category, "category")):
            await interaction.response.send_message(error, ephemeral=True)
            return
        if (error := validation.item_name_error(item, "item")):
            await interaction.response.send_message(error, ephemeral=True)
            return
        fields, error = validation.variant_fields(text, color, image, thumbnail)
        if (error):
            await interaction.response.send_message(error, ephemeral=True)
            return
        category = category.strip()
        item = item.strip()
        await interaction.response.defer(ephemeral=True)
        if (category not in await db_config.effective_category_sets(guild_id)):
            await interaction.followup.send(
                f"There is no roll category `{category}` in this server.",
                ephemeral=True)
            return
        active, _ = await db_config.list_category_items(guild_id, category)
        if (item in active):
            await interaction.followup.send(
                f"`{item}` is already an item of `{category}`.",
                ephemeral=True)
            return
        await db_config.add_item(guild_id, category, item, fields)
        await config_log.record_command_change(
            interaction, "rollset.item.add", f"{category} — {item}")
        await self.reload_config()
        await interaction.followup.send(
            f"Item `{item}` added to `{category}`.", ephemeral=True)

    @rollsets_item.command(name="rename", description="Rename an item.")
    @app_commands.describe(item="The item.", new_name="Its new name.")
    @app_commands.autocomplete(item=_item_autocomplete)
    async def item_rename(self, interaction: discord.Interaction, item: str,
                          new_name: str):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        if (error := validation.item_name_error(new_name, "new_name")):
            await interaction.response.send_message(error, ephemeral=True)
            return
        new_name = new_name.strip()
        await interaction.response.defer(ephemeral=True)
        resolved = await self._item_of(guild_id, item)
        if (resolved is None):
            await interaction.followup.send(
                f"There is no item `{item}` in this server.", ephemeral=True)
            return
        label = db_config.item_label(resolved)
        if (not await db_config.rename_item(guild_id, resolved.id, new_name)):
            await interaction.followup.send(
                f"`{new_name}` is already an item of "
                f"`{resolved.category.name}`.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "rollset.item.rename", f"{label} -> {new_name}")
        await self.reload_config()
        await interaction.followup.send(
            f"Item `{label}` renamed to `{new_name}`.", ephemeral=True)

    @rollsets_item.command(
        name="remove", description="Take an item out of its category's set (its descriptions are kept).")
    @app_commands.describe(item="The item.")
    @app_commands.autocomplete(item=_item_autocomplete)
    async def item_remove(self, interaction: discord.Interaction, item: str):
        if (not await self._guard_admin(interaction)):
            return
        if (not await self._guard_database(interaction)):
            return
        guild_id = await self._expect_guild(interaction)
        if (guild_id is None):
            return
        await interaction.response.defer(ephemeral=True)
        resolved = await self._item_of(guild_id, item)
        if (resolved is None):
            await interaction.followup.send(
                f"There is no item `{item}` in this server.", ephemeral=True)
            return
        label = db_config.item_label(resolved)
        await db_config.remove_item(guild_id, resolved.id)
        await config_log.record_command_change(
            interaction, "rollset.item.remove", label)
        await self.reload_config()
        await interaction.followup.send(
            f"Removed `{label}` from the set; its descriptions are kept, and "
            "adding the name back restores it.", ephemeral=True)

    # Attach the subgroups to the rollsets group now that their subcommands
    # exist; CogMeta skips them as top-level commands (parent set).
    rollsets.add_command(rollsets_description)
    rollsets.add_command(rollsets_item)

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

    async def reload_config(self) -> None:
        """Refresh the in-memory configuration from the database."""
        # Held across the reload: a second writer doing the same at once would
        # leave the cog reading a half-swapped configuration.
        async with self.config_lock:
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