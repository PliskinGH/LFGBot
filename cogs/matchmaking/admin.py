"""Admin slash commands: manage the server's games dynamically (database mode)."""

from typing import Optional

import discord
from discord import app_commands

from common import constants as common_constants
from common import utils as common_utils
from db import config_log

from . import constants
from . import db_config
from . import utils
from . import validation
from .models import GameOption

# Option descriptions shared by /games add and /games update.
_GAME_OPTION_DESCRIPTIONS = {
    "name": ("Display name of the game (at most 100 characters)."),
    "role": "Role or user mention to ping.",
    "icon": "Icon URL shown in embeds.",
    "color": "Embed color.",
    "channel": "LFG channel mention where the game's LFG posts go; blank keeps them in the command's channel.",
    "forum": "Forum channel mention for game threads.",
    "tag": "Forum tag name, as it appears on the target forum.",
    "visibility": "0 for private threads.",
    "message": "Extra message added to the game-start ping.",
    "registration_api": "League registration API URL.",
    "match_api": "League match API URL.",
    "match_url": "League match URL.",
    "api_token": "API token for match submissions; use `-` to clear it.",
    "website_url": "League website URL.",
    "registration_url": "League registration URL.",
    "profile_url": "League profile URL.",
    "max_players": "Default maximum players (2-100, host included).",
    "title_field": "Match API field receiving the title; `-` resets to the default.",
    "table_talk_url_field": "Match API field receiving the thread URL; `-` resets to the default.",
    "participants_field": "Match API field receiving the participants; `-` resets to the default.",
    "discord_username_field": "Match API field receiving each Discord name; `-` resets to the default.",
}

class LFGAdminMixin:
    """Permission-gated commands to edit the server's games.

    These write to the database (the runtime source of truth) and refresh the
    cog's in-memory configuration. They require the ``manage_guild``
    permission and only work when the bot runs in database mode
    (``DATABASE_URL`` set); config-file mode is read-only.
    """

    # The queued change actions this cog owns (see db/config_queue.py).
    change_prefixes = ("game.", "parameter.")

    games = app_commands.Group(
        name="games", description="Manage this server's games.",
        # Hide the whole /games group (including its subcommands) from
        # members without the manage_guild permission.
        default_permissions=discord.Permissions(manage_guild=True))

    # Nested subgroup (/games parameter ...), attached to the games group at
    # the end of this class so CogMeta registers it as a child, not a
    # top-level command.
    games_parameter = app_commands.Group(
        name="parameter", description="Manage a game's parameters.")

    async def _guard_admin(self, interaction: discord.Interaction) -> bool:
        """Reject non-managers with an ephemeral message."""
        permissions = getattr(interaction.user, "guild_permissions", None)
        if (permissions is None or not permissions.manage_guild):
            await interaction.response.send_message(
                "Only server managers can change the game configuration.",
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
        """Reload the configuration from the database and re-register the
        dynamic per-guild commands."""
        # Held across the reload and the re-registration: a second writer doing
        # the same at once would collide on the commands it re-adds.
        async with self.config_lock:
            loaded = await db_config.load_config_from_db()
            self.unregister_guild_commands()
            self.guilds = loaded.guilds
            self.default_guild_config = loaded.default_guild_config
            self.game_parameters = loaded.game_parameters
            self.game_api_fields = loaded.game_api_fields
            self.default_api_fields = loaded.default_api_fields
            self.register_guild_commands()

    async def sync_guild(self, guild_id: int) -> None:
        """Sync the guild's slash commands so the change applies immediately.

        Per-guild syncs are lenient (Discord's restrictive daily limit applies
        to global command creation), so syncing after each edit is safe. A
        failure here only delays the update: the startup sync in the bot's
        ``setup_hook`` re-syncs on the next restart.
        """
        print(f"Syncing per-guild commands for guild {guild_id}...")
        guild = discord.Object(id=guild_id)
        try:
            synced = await self.bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) for guild {guild_id}.")
        except Exception as error:
            print(f"Failed to sync commands for guild {guild_id}: {error}")

    async def sync_applied(self, guild_ids: set[int]) -> None:
        """Sync the commands of the guilds a queued change touched.

        A change to the [DEFAULT] configuration is stored under guild 0 and can
        alter the commands of every server the bot is in, so all of them are
        re-synced; guild 0 itself has no commands of its own.
        """
        if (constants.DEFAULT_GUILD_ID in guild_ids):
            guild_ids = {guild.id for guild in self.bot.guilds}
        for guild_id in sorted(guild_ids):
            await self.sync_guild(guild_id)

    @games.command(name="add", description="Add a game to this server.")
    @app_commands.describe(
        command="The slash command name (1-32 lowercase letters, digits or _).",
        **_GAME_OPTION_DESCRIPTIONS,
    )
    async def games_add(
        self,
        interaction: discord.Interaction,
        command: str,
        name: Optional[app_commands.Range[str, 1, constants.GAME_NAME_MAX]] = None,
        role: str = "",
        icon: str = "",
        color: str = "",
        channel: Optional[str] = None,
        forum: Optional[str] = None,
        tag: Optional[str] = None,
        visibility: Optional[str] = None,
        message: Optional[str] = None,
        registration_api: Optional[str] = None,
        match_api: Optional[str] = None,
        match_url: Optional[str] = None,
        api_token: Optional[str] = None,
        website_url: Optional[str] = None,
        registration_url: Optional[str] = None,
        profile_url: Optional[str] = None,
        max_players: Optional[int] = None,
        title_field: Optional[str] = None,
        table_talk_url_field: Optional[str] = None,
        participants_field: Optional[str] = None,
        discord_username_field: Optional[str] = None,
    ):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        if (not validation.is_valid_command_name(command)):
            await interaction.response.send_message(
                validation.invalid_command_message(command), ephemeral=True)
            return
        fields, error = validation.game_fields(
            name=name or "", role=role, icon=icon, color=color,
            channel=channel, forum=forum, tag=tag, visibility=visibility,
            message=message,
            registration_api=registration_api, match_api=match_api,
            match_url=match_url, api_token=api_token or "",
            website_url=website_url, registration_url=registration_url,
            profile_url=profile_url, max_players=max_players)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        api_fields, error = validation.api_fields_error({
            "title_field": title_field,
            "table_talk_url_field": table_talk_url_field,
            "participants_field": participants_field,
            "discord_username_field": discord_username_field,
        })
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return

        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id
        add_kwargs = dict(fields)
        if (api_fields):
            add_kwargs["api_fields"] = api_fields
        if (not await db_config.add_game(guild_id, command, **add_kwargs)):
            await interaction.followup.send(
                f"`{command}` is already configured; use `/games update` "
                "to change it.", ephemeral=True)
            return
        await config_log.record_command_change(interaction, "game.add", command)
        await self.reload_config()
        await self.sync_guild(interaction.guild_id)
        await interaction.followup.send(
            f"Game `{command}` added.", ephemeral=True)

    @games.command(
        name="copy",
        description="Copy a game, with its parameters, to a new command.")
    @app_commands.describe(
        game="The preset's slash command name to copy.",
        command="The new slash command name (1-32 lowercase letters, digits or _).",
        **{**_GAME_OPTION_DESCRIPTIONS,
           "name": "New display name (must differ from the copied game's)."},
    )
    async def games_copy(
        self,
        interaction: discord.Interaction,
        game: str,
        command: str,
        name: app_commands.Range[str, 1, constants.GAME_NAME_MAX],
        role: Optional[str] = None,
        icon: Optional[str] = None,
        color: Optional[str] = None,
        channel: Optional[str] = None,
        forum: Optional[str] = None,
        tag: Optional[str] = None,
        visibility: Optional[str] = None,
        message: Optional[str] = None,
        registration_api: Optional[str] = None,
        match_api: Optional[str] = None,
        match_url: Optional[str] = None,
        api_token: Optional[str] = None,
        website_url: Optional[str] = None,
        registration_url: Optional[str] = None,
        profile_url: Optional[str] = None,
        max_players: Optional[int] = None,
        title_field: Optional[str] = None,
        table_talk_url_field: Optional[str] = None,
        participants_field: Optional[str] = None,
        discord_username_field: Optional[str] = None,
    ):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        if (not validation.is_valid_command_name(command)):
            await interaction.response.send_message(
                validation.invalid_command_message(command), ephemeral=True)
            return
        guild_id = interaction.guild_id
        source_option = self.get_guild_config(guild_id).games.get(game)
        if (source_option is None):
            await interaction.response.send_message(
                f"`{game}` is not configured for this server.", ephemeral=True)
            return
        error = validation.copy_error(command, name, game, source_option.name)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        overrides, error = validation.updated_fields(
            name=name, role=role, icon=icon, color=color,
            channel=channel, forum=forum, tag=tag, visibility=visibility,
            message=message,
            registration_api=registration_api, match_api=match_api,
            match_url=match_url, api_token=api_token,
            website_url=website_url, registration_url=registration_url,
            profile_url=profile_url, max_players=max_players)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        api_fields, error = validation.api_fields_error({
            "title_field": title_field,
            "table_talk_url_field": table_talk_url_field,
            "participants_field": participants_field,
            "discord_username_field": discord_username_field,
        })
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return

        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        copied = await db_config.copy_game(
            guild_id, game, command, name=overrides.pop("name"),
            api_fields=api_fields or None, **overrides)
        if (not copied):
            await interaction.followup.send(
                f"`{command}` is already configured; use `/games update` "
                "to change it.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "game.copy", f"{command} from {game}")
        await self.reload_config()
        await self.sync_guild(interaction.guild_id)
        await interaction.followup.send(
            f"Game `{command}` copied from `{game}`.", ephemeral=True)

    @games_copy.autocomplete("game")
    async def games_copy_game_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    @games.command(name="update", description="Update an existing game on this server.")
    @app_commands.describe(
        command="The game's slash command name.",
        new_command="New slash command name, renaming the game (its parameters are kept).",
        **_GAME_OPTION_DESCRIPTIONS,
    )
    async def games_update(
        self,
        interaction: discord.Interaction,
        command: str,
        new_command: Optional[str] = None,
        name: Optional[app_commands.Range[str, 1, constants.GAME_NAME_MAX]] = None,
        role: Optional[str] = None,
        icon: Optional[str] = None,
        color: Optional[str] = None,
        channel: Optional[str] = None,
        forum: Optional[str] = None,
        tag: Optional[str] = None,
        visibility: Optional[str] = None,
        message: Optional[str] = None,
        registration_api: Optional[str] = None,
        match_api: Optional[str] = None,
        match_url: Optional[str] = None,
        api_token: Optional[str] = None,
        website_url: Optional[str] = None,
        registration_url: Optional[str] = None,
        profile_url: Optional[str] = None,
        max_players: Optional[int] = None,
        title_field: Optional[str] = None,
        table_talk_url_field: Optional[str] = None,
        participants_field: Optional[str] = None,
        discord_username_field: Optional[str] = None,
    ):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        if (new_command is not None):
            error = validation.rename_error(
                new_command, command,
                self.get_guild_config(interaction.guild_id).games)
            if (error is not None):
                await interaction.response.send_message(error, ephemeral=True)
                return
        fields, error = validation.updated_fields(
            name=name, role=role, icon=icon, color=color,
            channel=channel, forum=forum, tag=tag, visibility=visibility,
            message=message,
            registration_api=registration_api, match_api=match_api,
            match_url=match_url, api_token=api_token,
            website_url=website_url, registration_url=registration_url,
            profile_url=profile_url, max_players=max_players)
        api_fields, api_error = validation.api_fields_error({
            "title_field": title_field,
            "table_talk_url_field": table_talk_url_field,
            "participants_field": participants_field,
            "discord_username_field": discord_username_field,
        })
        if (api_error is not None):
            await interaction.response.send_message(api_error, ephemeral=True)
            return
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        if (not fields and not api_fields and new_command is None):
            await interaction.response.send_message(
                "Nothing to update: provide at least one option.", ephemeral=True)
            return

        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id
        update_kwargs = dict(fields)
        if (api_fields):
            update_kwargs["api_fields"] = api_fields
        if (new_command is not None):
            update_kwargs["new_command"] = new_command
        if (not await db_config.update_game(guild_id, command, **update_kwargs)):
            await interaction.followup.send(
                f"`{command}` is not configured for this server.", ephemeral=True)
            return
        if (new_command is not None):
            summary = f"{command} -> {new_command}"
        else:
            summary = ", ".join(sorted(update_kwargs))
        await config_log.record_command_change(
            interaction,
            "game.rename" if (new_command is not None) else "game.update",
            summary)
        await self.reload_config()
        await self.sync_guild(interaction.guild_id)
        if (new_command is not None):
            await interaction.followup.send(
                f"Game `{command}` renamed to `{new_command}`.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"Game `{command}` updated.", ephemeral=True)

    @games_update.autocomplete("command")
    async def games_update_command_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    @games.command(name="remove", description="Remove a game from this server.")
    @app_commands.describe(command="The game's slash command name.")
    async def games_remove(self, interaction: discord.Interaction, command: str):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        
        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        removed = await db_config.delete_game(interaction.guild_id, command)
        if (not removed):
            await interaction.followup.send(
                f"`{command}` is not configured for this server.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "game.remove", command)
        await self.reload_config()
        await self.sync_guild(interaction.guild_id)
        await interaction.followup.send(
            f"Game `{command}` removed.", ephemeral=True)

    @games_remove.autocomplete("command")
    async def games_remove_command_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    async def _games_autocomplete(
        self, interaction: discord.Interaction, current: str):
        guild_config = self.get_guild_config(interaction.guild_id)
        # Sorted: the stored order is the insertion order.
        return [
            app_commands.Choice(name=command, value=command)
            for command in sorted(guild_config.games, key=str.lower)
            if current.lower() in command.lower()
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    @games.command(name="list", description="List the games configured for this server.")
    async def games_list(self, interaction: discord.Interaction):
        # Gated like the rest of the /games group: it exposes api_fields and
        # other league configuration that should not be visible to members.
        if (not await self._guard_admin(interaction)):
            return
        games = self.get_guild_config(interaction.guild_id).games
        if (not games):
            await interaction.response.send_message(
                "No games are configured for this server.", ephemeral=True)
            return
        lines = []
        for command, option in sorted(
                games.items(),
                key=lambda item: (item[1].name or item[0]).lower()):
            details = []
            if (option.name):
                details.append(f"**{option.name}**")
            details.extend(option.config_summary())
            if (details):
                lines.append(f"`{command}` — " + " · ".join(details))
            else:
                lines.append(f"`{command}`")
        await interaction.response.send_message(
            common_utils.clip_lines(lines), ephemeral=True)

    def _game_show_output(self, guild_id: int, command: str,
                          option: GameOption) -> tuple[str, list[discord.Embed]]:
        """The /games show reply: a text summary plus detail embeds.

        The message content carries the game identity and its LFG post
        configuration (role, forum, thread visibility...), kept within
        Discord's 2000-character message limit. The "Match submission" embed
        holds the league endpoints and payload fields; the game settings get
        their own embed (chunked: an embed holds at most 25 fields). Secrets
        (the api token) are only ever indicated, never included.
        """
        if (option.name):
            content_lines = [f"**{option.name}** — `/{command}`"]
        else:
            content_lines = [f"`/{command}`"]
        if (option.icon):
            content_lines.append(f"Icon: {option.icon}")
        if (option.color):
            content_lines.append(f"Color: {option.color}")
        if (option.role):
            content_lines.append(f"Role to ping: {option.role}")
        if (option.channel):
            content_lines.append(f"LFG channel: {option.channel}")
        if (option.forum):
            forum = f"Forum: {option.forum}"
            if (option.tag):
                forum += f" (tag: {option.tag})"
            content_lines.append(forum)
        if (option.visibility):
            content_lines.append("Threads: private" if (option.visibility == "0")
                                 else "Threads: public")
        if (option.message):
            content_lines.append(f'Extra message: "{option.message}"')
        if (option.default_max_guests is not None):
            content_lines.append(
                f"Default max players: {option.default_max_guests + 1}")

        submission_embed = discord.Embed(title="Match submission")
        for label, url in (
            ("Website URL", option.website_url),
            ("Profile URL", option.profile_url),
            ("Registration URL", option.registration_url),
            ("Match URL", option.match_url),
            ("Registration API", option.registration_api),
            ("Match API", option.match_api)):
            if (url):
                submission_embed.add_field(
                    name=label, value=common_utils.shorten(url), inline=False)
        payload_fields = dict(self.default_api_fields)
        payload_fields.update(self.get_game_api_fields(guild_id, command))
        payload_bits = [f"{label} → `{payload_fields[key]}`"
                        for label, key in (
            ("title", constants.API_TITLE_FIELD_KEY),
            ("thread link", constants.API_TABLE_TALK_URL_FIELD_KEY),
            ("participants", constants.API_PARTICIPANTS_FIELD_KEY),
            ("discord name", constants.API_DISCORD_USERNAME_FIELD_KEY),
        ) if (payload_fields.get(key))]
        if (payload_bits):
            submission_embed.add_field(
                name="Payload fields",
                value=common_utils.shorten(" · ".join(payload_bits)), inline=False)
        # The token itself is a secret and is never displayed; admins only
        # see whether one is configured.
        submission_embed.add_field(
            name="API token",
            value="set" if (option.api_token) else "not set", inline=False)
        parameters = self.get_game_parameters(guild_id, command)
        api_fields = self.get_game_api_fields(guild_id, command)
        if (not parameters):
            content_lines.append("Parameters: none")
            return common_utils.clip_lines(content_lines), [submission_embed]
        fields = []
        for name, definition in parameters.items():
            display = definition["display_name"]
            field_name = (f"{name} ({display})"
                          if (display != name) else name)
            value = utils.format_accepted_values(definition["values"])
            field = api_fields.get(name)
            value += f"\nSent as `{field}`" if (field) else "\nDiscord-only"
            fields.append((common_utils.shorten(field_name, 256),
                           common_utils.shorten(value)))
        # Discord caps an embed at 25 fields: chunk large parameter sets
        # into follow-up embeds.
        parameter_embeds = []
        for index, start in enumerate(range(0, len(fields), 25)):
            embed = discord.Embed(
                title=f"Parameters — /{command}"
                      + (" (continued)" if (index) else ""))
            for field_name, value in fields[start:start + 25]:
                embed.add_field(name=field_name, value=value, inline=False)
            parameter_embeds.append(embed)
        return (common_utils.clip_lines(content_lines),
                [submission_embed] + parameter_embeds)

    @games.command(name="show",
                   description="Show everything configured for a game.")
    @app_commands.describe(game="The game's slash command name.")
    async def games_show(self, interaction: discord.Interaction, game: str):
        # Like /games list, this exposes api_fields and other league configuration
        # that should not be visible to members.
        if (not await self._guard_admin(interaction)):
            return
        option = self.get_guild_config(interaction.guild_id).games.get(game)
        if (option is None):
            await interaction.response.send_message(
                f"`{game}` is not configured for this server.", ephemeral=True)
            return
        content, embeds = self._game_show_output(
            interaction.guild_id, game, option)
        await interaction.response.send_message(
            content=content, embeds=embeds, ephemeral=True)

    @games_show.autocomplete("game")
    async def games_show_game_autocomplete(
            self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    # ------------------------------------------------------------------ #
    # /games parameter — edit a game's parameters for this server.
    # ------------------------------------------------------------------ #

    async def _parameters_autocomplete(
        self, interaction: discord.Interaction, game_command: str,
        current: str):
        parameters = self.get_game_parameters(interaction.guild_id, game_command)
        return [
            app_commands.Choice(name=name, value=name)
            for name in parameters
            if current.lower() in name.lower()
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    async def _parameter_name_autocomplete(
        self, interaction: discord.Interaction, current: str):
        # The game option is filled in before this option's autocomplete runs.
        game_command = getattr(getattr(interaction, "namespace", None), "game", None)
        if (game_command is None):
            return []
        return await self._parameters_autocomplete(interaction, game_command, current)

    @games_parameter.command(name="add", description="Add a parameter to a game.")
    @app_commands.describe(
        game="The game's slash command name.",
        name="Parameter name (1-32 lowercase letters, digits or _).",
        display_name="Optional user-facing label (defaults to the name).",
        values="Accepted values, comma-separated; use (value, Display) pairs for display names.",
        api_field="Optional match API field the values are submitted as.",
    )
    async def games_parameter_add(
        self, interaction: discord.Interaction, game: str, name: str,
        values: str, api_field: Optional[str] = None,
        display_name: Optional[str] = None,
    ):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        error = validation.parameter_error(name, values, api_field, display_name)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        guild_id = interaction.guild_id
        if (game not in self.get_guild_config(guild_id).games):
            await interaction.response.send_message(
                f"`{game}` is not configured for this server.", ephemeral=True)
            return
        
        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        if (not await db_config.add_parameter(
                guild_id, game, name,
                utils.parse_param_entries(values), api_field=api_field,
                display_name=display_name)):
            await interaction.followup.send(
                f"`{game}` already has a parameter named `{name}`.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "parameter.add", f"{game}/{name}")
        await self.reload_config()
        await self.sync_guild(interaction.guild_id)
        await interaction.followup.send(
            f"Parameter `{name}` added to `{game}`.", ephemeral=True)

    @games_parameter_add.autocomplete("game")
    async def games_parameter_add_game_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)
    @games_parameter.command(name="update", description="Update a game's parameter.")
    @app_commands.describe(
        game="The game's slash command name.",
        name="The parameter name.",
        display_name="Optional user-facing label; use `-` to reset it to the name.",
        values="Accepted values, comma-separated; use (value, Display) pairs for display names.",
        api_field="Optional match API field; use `-` to clear it.",
    )
    async def games_parameter_update(
        self, interaction: discord.Interaction, game: str, name: str,
        values: Optional[str] = None, api_field: Optional[str] = None,
        display_name: Optional[str] = None,
    ):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        if (values is None and api_field is None and display_name is None):
            await interaction.response.send_message(
                "Nothing to update: provide `values`, `api_field` and/or `display_name`.",
                ephemeral=True)
            return
        # Discord cannot send an empty string: leaving an option blank omits
        # it entirely. "-" is the reset sentinel for api_field/display_name,
        # turned into "" (which the validation and db_config treat as reset).
        if (api_field is not None and api_field.strip() == common_constants.RESET_SENTINEL):
            api_field = ""
        if (display_name is not None and display_name.strip() == common_constants.RESET_SENTINEL):
            display_name = ""
        guild_id = interaction.guild_id
        if (game not in self.get_guild_config(guild_id).games):
            await interaction.response.send_message(
                f"`{game}` is not configured for this server.", ephemeral=True)
            return
        error = validation.parameter_error(name, values, api_field, display_name)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        value_display = None
        if (values is not None):
            value_display = utils.parse_param_entries(values)

        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        update_kwargs = {"values": value_display}
        if (api_field is not None):
            update_kwargs["api_field"] = api_field
        if (display_name is not None):
            update_kwargs["display_name"] = display_name
        updated = await db_config.update_parameter(
            guild_id, game, name, **update_kwargs)
        if (not updated):
            await interaction.followup.send(
                f"`{game}` has no parameter named `{name}`.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "parameter.update", f"{game}/{name}")
        await self.reload_config()
        await self.sync_guild(interaction.guild_id)
        await interaction.followup.send(
            f"Parameter `{name}` updated.", ephemeral=True)

    @games_parameter_update.autocomplete("game")
    async def games_parameter_update_game_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    @games_parameter_update.autocomplete("name")
    async def games_parameter_update_name_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._parameter_name_autocomplete(interaction, current)
    @games_parameter.command(name="remove", description="Remove a parameter from a game.")
    @app_commands.describe(
        game="The game's slash command name.",
        name="The parameter name.",
    )
    async def games_parameter_remove(
        self, interaction: discord.Interaction, game: str, name: str):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        guild_id = interaction.guild_id
        if (game not in self.get_guild_config(guild_id).games):
            await interaction.response.send_message(
                f"`{game}` is not configured for this server.", ephemeral=True)
            return
        
        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)
        
        if (not await db_config.delete_parameter(guild_id, game, name)):
            await interaction.followup.send(
                f"`{game}` has no parameter named `{name}`.", ephemeral=True)
            return
        await config_log.record_command_change(
            interaction, "parameter.remove", f"{game}/{name}")
        await self.reload_config()
        await self.sync_guild(interaction.guild_id)
        await interaction.followup.send(
            f"Parameter `{name}` removed from `{game}`.", ephemeral=True)

    @games_parameter_remove.autocomplete("game")
    async def games_parameter_remove_game_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    @games_parameter_remove.autocomplete("name")
    async def games_parameter_remove_name_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._parameter_name_autocomplete(interaction, current)

    @games_parameter.command(name="list", description="List a game's parameters.")
    @app_commands.describe(game="The game's slash command name.")
    async def games_parameter_list(self, interaction: discord.Interaction, game: str):
        # Gated like the rest of the /games group: it exposes api_fields that
        # should not be visible to regular members.
        if (not await self._guard_admin(interaction)):
            return
        parameters = self.get_game_parameters(interaction.guild_id, game)
        if (not parameters):
            await interaction.response.send_message(
                f"`{game}` has no parameters configured on this server.",
                ephemeral=True)
            return
        api_fields = self.get_game_api_fields(interaction.guild_id, game)
        lines = []
        for name, parameter in parameters.items():
            field = api_fields.get(name)
            values_summary = utils.format_accepted_values(parameter["values"])
            display_name = parameter.get("display_name", name)
            label = (name if display_name == name
                     else f"{name} ({display_name})")
            if (field):
                lines.append(f"`{label}` → `{field}`: {values_summary}")
            else:
                lines.append(f"`{label}`: {values_summary}")
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True)

    @games_parameter_list.autocomplete("game")
    async def games_parameter_list_game_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    # Attach the parameter subgroup to the games group now that its
    # subcommands exist; CogMeta skips it as a top-level command (parent set).
    games.add_command(games_parameter)

    # End of LFGAdminMixin.

