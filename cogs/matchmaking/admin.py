"""Admin slash commands: manage the server's games dynamically (database mode)."""

from typing import Optional

import discord
from discord import app_commands

from common import constants as common_constants

from . import constants
from . import db_config
from . import utils
from .config import ConfigMixin
from .models import GameOption

# Option descriptions shared by /games add and /games update.
_GAME_OPTION_DESCRIPTIONS = {
    "name": "Display name of the game.",
    "role": "Role or user mention to ping.",
    "icon": "Icon URL shown in embeds.",
    "color": "Embed color.",
    "forum": "Forum channel mention for game threads.",
    "tag": "Forum tag id.",
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

# Reserved match-payload component fields: /games add|update argument name
# -> canonical api_* key stored as a per-game override of the default payload
# field names. Unlike the game columns above they are not Game model fields.
_API_FIELD_ARGUMENTS = {
    "title_field": constants.API_TITLE_FIELD_KEY,
    "table_talk_url_field": constants.API_TABLE_TALK_URL_FIELD_KEY,
    "participants_field": constants.API_PARTICIPANTS_FIELD_KEY,
    "discord_username_field": constants.API_DISCORD_USERNAME_FIELD_KEY,
}


class AdminMixin:
    """Permission-gated commands to edit the server's games.

    These write to the database (the runtime source of truth) and refresh the
    cog's in-memory configuration. They require the ``manage_guild``
    permission and only work when the bot runs in database mode
    (``DATABASE_URL`` set); config-file mode is read-only.
    """

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

    @staticmethod
    def is_valid_command_name(name: str) -> bool:
        """Whether ``name`` can become a Discord slash command."""
        return bool(common_constants.COMMAND_NAME_RE.match(name))

    @staticmethod
    def _parameter_error(name: str, values: str | None,
                         api_field: str | None = None,
                         display_name: str | None = None) -> str | None:
        """An error message when a parameter name/values/api_field are invalid, else None."""
        if (not common_constants.COMMAND_NAME_RE.match(name)):
            return "`name` must be 1-32 lowercase letters, digits or underscores."
        if (name.startswith(constants.API_FIELD_PREFIX)):
            return f"`name` cannot start with `{constants.API_FIELD_PREFIX}` (reserved)."
        if (values is not None and not utils.parse_param_entries(values or "")):
            return "`values` must contain at least one value."
        # Blank (empty string) is valid: it resets the API field (db_config
        # turns "" into NULL); on add it means "no field". The update command
        # accepts "-" as the reset sentinel, since Discord cannot send "".
        if (api_field and not common_constants.API_FIELD_RE.match(api_field)):
            return ("`api_field` must be a non-empty field name (letters, digits,"
                    " underscores), or `-` to reset it.")
        if (display_name is not None and display_name != "" and (
                not display_name.strip() or len(display_name) > 50
                or "\n" in display_name or "\r" in display_name)):
            return "`display_name` must be 1-50 characters without newlines."
        return None

    @staticmethod
    def _mention_error(role: str | None, forum: str | None) -> str | None:
        """An error message when role/forum are not Discord mentions, else None.

        The values must be valid mentions (roles: role/user mentions; forums:
        channel mentions); anything else would not resolve at game start.
        """
        if (role and not common_constants.ROLE_MENTION_RE.match(role)):
            return "`role` must be a role or user mention."
        if (forum and not common_constants.CHANNEL_MENTION_RE.match(forum)):
            return "`forum` must be a channel mention."
        return None

    @staticmethod
    def _api_fields_error(
            values: dict[str, Optional[str]]
    ) -> tuple[dict[str, Optional[str]], Optional[str]]:
        """The reserved api_* overrides from /games add|update arguments.

        Returns ``(api_fields, error)``: a mapping of canonical api_* keys to
        the requested field name (``None`` = clear the override, falling back
        to the default), or an error message when a value is malformed.
        Arguments left out (None) keep the current override untouched.
        """
        api_fields = {}
        for argument, key in _API_FIELD_ARGUMENTS.items():
            value = values.get(argument)
            if (value is None):
                continue
            if (value == "-"):
                api_fields[key] = None
            elif (not common_constants.API_FIELD_RE.match(value)):
                return {}, (f"`{argument}` must be a non-empty field name "
                            "(letters, digits, underscores), or `-` to reset "
                            "it to the default.")
            else:
                api_fields[key] = value
        return api_fields, None

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

    @staticmethod
    def _game_fields(
        name="", role="", icon="", color="",
        forum=None, tag=None, visibility=None, message=None,
        registration_api=None, match_api=None, match_url=None,
        api_token="",
        website_url=None, registration_url=None, profile_url=None,
        max_players=None,
    ) -> tuple[dict, str | None]:
        """The Game fields from add options; returns (fields, error message).

        ``api_token`` is the token VALUE (a secret, never displayed): config
        files resolve their env var at load time, admins set it via /games.
        """
        mention_error = AdminMixin._mention_error(role, forum)
        if (mention_error is not None):
            return None, mention_error
        default_max_guests = None
        if (max_players is not None):
            default_max_guests = ConfigMixin.parse_default_max_guests(str(max_players))
            if (default_max_guests is None):
                return None, "`max_players` must be between 2 and 100."
        fields = {
            "name": name,
            "role": role,
            "icon": icon,
            "color": color,
            "forum": forum,
            "tag": tag,
            "visibility": visibility,
            "message": message,
            "registration_api": registration_api,
            "match_api": match_api,
            "match_url": match_url,
            "api_token": api_token,
            "website_url": website_url,
            "registration_url": registration_url,
            "profile_url": profile_url,
            "default_max_guests": default_max_guests,
        }
        return fields, None

    @staticmethod
    def _updated_fields(
        name=None, role=None, icon=None, color=None,
        forum=None, tag=None, visibility=None, message=None,
        registration_api=None, match_api=None, match_url=None,
        api_token=None,
        website_url=None, registration_url=None, profile_url=None,
        max_players=None,
    ) -> tuple[dict, str | None]:
        """The Game fields to change from update options; (fields, error).

        Only the provided options are touched; omitted ones keep their value.
        ``api_token`` accepts ``-`` as the reset sentinel (Discord cannot send
        an empty string), which clears the token.
        """
        mention_error = AdminMixin._mention_error(role, forum)
        if (mention_error is not None):
            return None, mention_error
        fields = {}
        for field_name, value in (
            ("name", name), ("role", role), ("icon", icon), ("color", color),
            ("forum", forum), ("tag", tag), ("visibility", visibility),
            ("message", message),
            ("registration_api", registration_api), ("match_api", match_api),
            ("match_url", match_url),
            ("api_token", "" if (api_token == "-") else api_token),
            ("website_url", website_url), ("registration_url", registration_url),
            ("profile_url", profile_url),
        ):
            if (value is not None):
                fields[field_name] = value
        if (max_players is not None):
            default_max_guests = ConfigMixin.parse_default_max_guests(str(max_players))
            if (default_max_guests is None):
                return None, "`max_players` must be between 2 and 100."
            fields["default_max_guests"] = default_max_guests
        return fields, None

    async def _refresh_config(self) -> None:
        """Reload the configuration from the database and re-register the
        dynamic per-guild commands."""
        loaded = await db_config.load_config_from_db()
        self.unregister_guild_commands()
        self.guilds = loaded.guilds
        self.default_guild_config = loaded.default_guild_config
        self.game_parameters = loaded.game_parameters
        self.game_api_fields = loaded.game_api_fields
        self.default_api_fields = loaded.default_api_fields
        self.register_guild_commands()

    async def _sync_guild(self, interaction: discord.Interaction) -> None:
        """Sync the guild's slash commands so the change applies immediately.

        Per-guild syncs are lenient (Discord's restrictive daily limit applies
        to global command creation), so syncing after each edit is safe. A
        failure here only delays the update: the startup sync in the bot's
        ``setup_hook`` re-syncs on the next restart.
        """
        guild_id = interaction.guild_id
        if (guild_id is None):
            return
        
        print(f"Syncing per-guild commands for guild {guild_id}...")
        guild = discord.Object(id=guild_id)
        try:
            synced = await self.bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) for guild {guild_id}.")
        except Exception as error:
            print(f"Failed to sync commands for guild {guild_id}: {error}")

    @games.command(name="add", description="Add a game to this server.")
    @app_commands.describe(
        command="The slash command name (1-32 lowercase letters, digits or _).",
        **_GAME_OPTION_DESCRIPTIONS,
    )
    async def games_add(
        self,
        interaction: discord.Interaction,
        command: str,
        name: str = "",
        role: str = "",
        icon: str = "",
        color: str = "",
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
        if (not self.is_valid_command_name(command)):
            await interaction.response.send_message(
                f"`{command}` is not a valid slash command name: use 1-32 "
                "lowercase letters, digits or underscores.",
                ephemeral=True)
            return
        fields, error = self._game_fields(
            name=name, role=role, icon=icon, color=color,
            forum=forum, tag=tag, visibility=visibility, message=message,
            registration_api=registration_api, match_api=match_api,
            match_url=match_url, api_token=api_token or "",
            website_url=website_url, registration_url=registration_url,
            profile_url=profile_url, max_players=max_players)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        api_fields, error = self._api_fields_error({
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
        await db_config.ensure_guild_config(guild_id)
        add_kwargs = dict(fields)
        if (api_fields):
            add_kwargs["api_fields"] = api_fields
        if (not await db_config.add_game(guild_id, command, **add_kwargs)):
            await interaction.followup.send(
                f"`{command}` is already configured; use `/games update` "
                "to change it.", ephemeral=True)
            return
        await self._refresh_config()
        await self._sync_guild(interaction)
        await interaction.followup.send(
            f"Game `{command}` added.", ephemeral=True)

    @games.command(name="update", description="Update an existing game on this server.")
    @app_commands.describe(
        command="The game's slash command name.",
        **_GAME_OPTION_DESCRIPTIONS,
    )
    async def games_update(
        self,
        interaction: discord.Interaction,
        command: str,
        name: Optional[str] = None,
        role: Optional[str] = None,
        icon: Optional[str] = None,
        color: Optional[str] = None,
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
        fields, error = self._updated_fields(
            name=name, role=role, icon=icon, color=color,
            forum=forum, tag=tag, visibility=visibility, message=message,
            registration_api=registration_api, match_api=match_api,
            match_url=match_url, api_token=api_token,
            website_url=website_url, registration_url=registration_url,
            profile_url=profile_url, max_players=max_players)
        api_fields, api_error = self._api_fields_error({
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
        if (not fields and not api_fields):
            await interaction.response.send_message(
                "Nothing to update: provide at least one option.", ephemeral=True)
            return

        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id
        update_kwargs = dict(fields)
        if (api_fields):
            update_kwargs["api_fields"] = api_fields
        if (not await db_config.update_game(guild_id, command, **update_kwargs)):
            await interaction.followup.send(
                f"`{command}` is not configured for this server.", ephemeral=True)
            return
        await self._refresh_config()
        await self._sync_guild(interaction)
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
        await self._refresh_config()
        await self._sync_guild(interaction)
        await interaction.followup.send(
            f"Game `{command}` removed.", ephemeral=True)

    @games_remove.autocomplete("command")
    async def games_remove_command_autocomplete(
        self, interaction: discord.Interaction, current: str):
        return await self._games_autocomplete(interaction, current)

    async def _games_autocomplete(
        self, interaction: discord.Interaction, current: str):
        guild_config = self.get_guild_config(interaction.guild_id)
        return [
            app_commands.Choice(name=command, value=command)
            for command in guild_config.games
            if current.lower() in command.lower()
        ][:common_constants.AUTOCOMPLETE_LIMIT]

    @games.command(name="list", description="List the games configured for this server.")
    async def games_list(self, interaction: discord.Interaction):
        # Gated like the rest of the /games group: it exposes api_fields and
        # other league settings that should not be visible to regular members.
        if (not await self._guard_admin(interaction)):
            return
        games = self.get_guild_config(interaction.guild_id).games
        if (not games):
            await interaction.response.send_message(
                "No games are configured for this server.", ephemeral=True)
            return
        lines = []
        for command, option in games.items():
            details = []
            if (option.name):
                details.append(f"**{option.name}**")
            details.extend(option.settings_summary())
            if (details):
                lines.append(f"`{command}` — " + " · ".join(details))
            else:
                lines.append(f"`{command}`")
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True)

    def _game_details(self, guild_id: int, command: str,
                      option: GameOption) -> str:
        """Everything configured for a game, as one ephemeral text block.

        Secrets (the api token) are only ever indicated, never included.
        """
        if (option.name):
            lines = [f"**{option.name}** — `/{command}`"]
        else:
            lines = [f"`/{command}`"]
        if (option.role):
            lines.append(f"Role to ping: {option.role}")
        if (option.forum):
            forum = f"Forum: {option.forum}"
            if (option.tag):
                forum += f" (tag: {option.tag})"
            lines.append(forum)
        if (option.visibility):
            lines.append("Threads: private" if (option.visibility == "0")
                         else "Threads: public")
        if (option.message):
            lines.append(f'Extra message: "{option.message}"')
        if (option.default_max_guests is not None):
            lines.append(f"Default max players: {option.default_max_guests + 1}")
        if (option.registration_api):
            lines.append(f"Registration API: {option.registration_api}")
        if (option.match_api):
            lines.append(f"Match API: {option.match_api}")
        if (option.match_url):
            lines.append(f"Match URL: {option.match_url}")
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
            lines.append("API payload fields: " + " · ".join(payload_bits))
        links = [f"{label}: {url}" for label, url in (
            ("website", option.website_url),
            ("registration", option.registration_url),
            ("profile", option.profile_url)) if (url)]
        if (links):
            lines.append("Links: " + " · ".join(links))
        # The token itself is a secret and is never displayed; admins only
        # see whether one is configured.
        lines.append("API token: set" if (option.api_token)
                     else "API token: not set")
        parameters = self.get_game_parameters(guild_id, command)
        api_fields = self.get_game_api_fields(guild_id, command)
        if (parameters):
            lines.append("Parameters:")
            for name, definition in parameters.items():
                display = definition["display_name"]
                label = f"`{name}`" + (f" ({display})" if (display != name) else "")
                values = ", ".join(
                    f"`{value}`" + (f" ({display_name})"
                                    if (display_name != value) else "")
                    for value, display_name in definition["values"].items())
                field = api_fields.get(name)
                suffix = f" — sent as `{field}`" if (field) else " — Discord-only"
                lines.append(f"- {label}: {values or 'no values'}{suffix}")
        else:
            lines.append("Parameters: none")
        return "\n".join(lines)

    @games.command(name="show",
                   description="Show everything configured for a game.")
    @app_commands.describe(game="The game's slash command name.")
    async def games_show(self, interaction: discord.Interaction, game: str):
        # Like /games list, this exposes api_fields and other league settings
        # that should not be visible to regular members.
        if (not await self._guard_admin(interaction)):
            return
        option = self.get_guild_config(interaction.guild_id).games.get(game)
        if (option is None):
            await interaction.response.send_message(
                f"`{game}` is not configured for this server.", ephemeral=True)
            return
        await interaction.response.send_message(
            self._game_details(interaction.guild_id, game, option),
            ephemeral=True)

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
        error = self._parameter_error(name, values, api_field, display_name)
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

        await db_config.ensure_guild_config(guild_id)
        if (not await db_config.add_parameter(
                guild_id, game, name,
                utils.parse_param_entries(values), api_field=api_field,
                display_name=display_name)):
            await interaction.followup.send(
                f"`{game}` already has a parameter named `{name}`.", ephemeral=True)
            return
        await self._refresh_config()
        await self._sync_guild(interaction)
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
        if (api_field is not None and api_field.strip() == "-"):
            api_field = ""
        if (display_name is not None and display_name.strip() == "-"):
            display_name = ""
        guild_id = interaction.guild_id
        if (game not in self.get_guild_config(guild_id).games):
            await interaction.response.send_message(
                f"`{game}` is not configured for this server.", ephemeral=True)
            return
        error = self._parameter_error(name, values, api_field, display_name)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        value_display = None
        if (values is not None):
            value_display = utils.parse_param_entries(values)

        # Defer to avoid the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        await db_config.ensure_guild_config(guild_id)
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
        await self._refresh_config()
        await self._sync_guild(interaction)
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
        
        await db_config.ensure_guild_config(guild_id)
        if (not await db_config.delete_parameter(guild_id, game, name)):
            await interaction.followup.send(
                f"`{game}` has no parameter named `{name}`.", ephemeral=True)
            return
        await self._refresh_config()
        await self._sync_guild(interaction)
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

    # End of AdminMixin.

