"""Admin slash commands: manage the server's games dynamically (database mode)."""

from typing import Optional

import discord
from discord import app_commands

from common import constants as common_constants

from . import db_config
from .config import ConfigMixin

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
    "website_url": "League website URL.",
    "registration_url": "League registration URL.",
    "profile_url": "League profile URL.",
    "max_players": "Default maximum players (2-100, host included).",
}


class AdminMixin:
    """Permission-gated commands to edit the server's games.

    These write to the database (the runtime source of truth) and refresh the
    cog's in-memory configuration. They require the ``manage_guild``
    permission and only work when the bot runs in database mode
    (``DATABASE_URL`` set); config-file mode is read-only.
    """

    games = app_commands.Group(
        name="games", description="Manage this server's games.")

    @staticmethod
    def is_valid_command_name(name: str) -> bool:
        """Whether ``name`` can become a Discord slash command."""
        return bool(common_constants.COMMAND_NAME_RE.match(name))

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
        website_url=None, registration_url=None, profile_url=None,
        max_players=None,
    ) -> tuple[dict, str | None]:
        """The Game fields from add options; returns (fields, error message).

        ``api_token_env_var`` is deliberately not settable here: it names an
        environment variable of the bot, which a server admin does not control.
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
        website_url=None, registration_url=None, profile_url=None,
        max_players=None,
    ) -> tuple[dict, str | None]:
        """The Game fields to change from update options; (fields, error).

        Only the provided options are touched; omitted ones keep their value.
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
        if (not fields):
            return None, "Nothing to update: provide at least one option."
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
        website_url: Optional[str] = None,
        registration_url: Optional[str] = None,
        profile_url: Optional[str] = None,
        max_players: Optional[int] = None,
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
            match_url=match_url,
            website_url=website_url, registration_url=registration_url,
            profile_url=profile_url, max_players=max_players)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        guild_id = interaction.guild_id
        await db_config.ensure_guild_config(guild_id)
        if (not await db_config.add_game(guild_id, command, **fields)):
            await interaction.response.send_message(
                f"`{command}` is already configured; use `/games update` "
                "to change it.", ephemeral=True)
            return
        await self._refresh_config()
        await self._sync_guild(interaction)
        await interaction.response.send_message(
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
        website_url: Optional[str] = None,
        registration_url: Optional[str] = None,
        profile_url: Optional[str] = None,
        max_players: Optional[int] = None,
    ):
        if (not await self._guard_admin(interaction)
                or not await self._guard_database(interaction)):
            return
        fields, error = self._updated_fields(
            name=name, role=role, icon=icon, color=color,
            forum=forum, tag=tag, visibility=visibility, message=message,
            registration_api=registration_api, match_api=match_api,
            match_url=match_url,
            website_url=website_url, registration_url=registration_url,
            profile_url=profile_url, max_players=max_players)
        if (error is not None):
            await interaction.response.send_message(error, ephemeral=True)
            return
        guild_id = interaction.guild_id
        if (not await db_config.update_game(guild_id, command, **fields)):
            await interaction.response.send_message(
                f"`{command}` is not configured for this server.", ephemeral=True)
            return
        await self._refresh_config()
        await self._sync_guild(interaction)
        await interaction.response.send_message(
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
        removed = await db_config.delete_game(interaction.guild_id, command)
        if (not removed):
            await interaction.response.send_message(
                f"`{command}` is not configured for this server.", ephemeral=True)
            return
        await self._refresh_config()
        await self._sync_guild(interaction)
        await interaction.response.send_message(
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

