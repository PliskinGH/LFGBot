from typing import Any, Callable, Coroutine
import discord
from discord import app_commands
from discord.ext import commands
import configparser
import re
import os, aiohttp

from common.ui import DynamicButtonView, DynamicSelectView
from common import utils, common

LFG_COMMAND = "lfg"
LFG_DESCRIPTION = "Looking for a game."
LFG_JOIN_BUTTON_LABEL = "Join/Leave"
LFG_NOTIFY_BUTTON_LABEL = "Toggle Notification"
LFG_CANCEL_BUTTON_LABEL = "Cancel"
LFG_START_BUTTON_LABEL = "Start"
LFG_JOIN_CUSTOM_ID = "lfg_view:join"
LFG_NOTIFY_CUSTOM_ID = "lfg_view:notify"
LFG_CANCEL_CUSTOM_ID = "lfg_view:cancel"
LFG_START_CUSTOM_ID = "lfg_view:start"

CONFIG_GAMES_COMMANDS = "GamesCommands"
CONFIG_GAMES_NAMES = "GamesFullNames"
CONFIG_GAMES_ROLES = "GamesRoles"
CONFIG_GAMES_ICONS = "GamesIcons"
CONFIG_GAMES_COLORS = "GamesColors"
CONFIG_GAMES_FORUMS = "GamesForums"
CONFIG_GAMES_TAGS = "GamesTags"
CONFIG_GAMES_VISIBILITY = "GamesVisibility"
CONFIG_GAMES_MESSAGES = "GamesMessages"
CONFIG_GAMES_REGISTRATION_API = "GamesRegistrationAPI"
CONFIG_GAMES_MATCH_API = "GamesMatchAPI"
CONFIG_GAMES_MATCH_URL = "GamesMatchURL"
CONFIG_GAMES_API_TOKEN_ENV_VARS = "GamesAPITokenEnvVars"
CONFIG_GAMES_WEBSITE_URL = "GamesWebsiteURL"
CONFIG_GAMES_REGISTRATION_URL = "GamesRegistrationURL"
CONFIG_GAMES_PROFILE_URL = "GamesProfileURL"
CONFIG_GAMES_MAX_PLAYERS = "GamesMaxPlayers"
CONFIG_GAMES_ARGS = [
    CONFIG_GAMES_COMMANDS,
    CONFIG_GAMES_NAMES,
    CONFIG_GAMES_ROLES,
    CONFIG_GAMES_ICONS,
    CONFIG_GAMES_COLORS,
    CONFIG_GAMES_FORUMS,
    CONFIG_GAMES_TAGS,
    CONFIG_GAMES_VISIBILITY,
    CONFIG_GAMES_MESSAGES,
    CONFIG_GAMES_REGISTRATION_API,
    CONFIG_GAMES_MATCH_API,
    CONFIG_GAMES_MATCH_URL,
    CONFIG_GAMES_API_TOKEN_ENV_VARS,
    CONFIG_GAMES_WEBSITE_URL,
    CONFIG_GAMES_REGISTRATION_URL,
    CONFIG_GAMES_PROFILE_URL,
    CONFIG_GAMES_MAX_PLAYERS
]
EMOJI_JOIN = "👍"
EMOJI_NOTIFY = "🔔"
EMOJI_CANCEL = "❌"
EMOJI_START = "✅"
EMOJIS_VALID = [EMOJI_JOIN, EMOJI_NOTIFY, EMOJI_CANCEL, EMOJI_START]
EMOJIS_CLOSE = [EMOJI_CANCEL, EMOJI_START]

LFG_FOOTER_HELP = "For discussion about this game, please use a thread.\nIt will be created for you when you close the game."

THREAD_TYPES = [discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
                discord.ChannelType.news_thread]

GUESTS_OVER_LIMIT = " and others..."

class GameOption(object):

    def __init__(self, name, command, role, icon, color, forum,
                 tag, visibility, message,
                 registration_api, match_api,
                 match_url,
                 api_token_env_var, website_url,
                 registration_url, profile_url,
                 default_max_guests):
        self.name = name
        self.command = command
        self.role = role
        self.icon = icon
        self.color = color
        self.forum = forum
        self.tag = tag
        self.visibility = visibility
        self.message = message
        self.registration_api = registration_api
        self.match_api = match_api
        self.match_url = match_url
        self.api_token_env_var = api_token_env_var
        self.website_url = website_url
        self.registration_url = registration_url
        self.profile_url = profile_url
        self.default_max_guests = default_max_guests

class GuildGamesConfig(object):

    def __init__(self, guild_id : int):
        self.guild_id = guild_id
        self.games : dict[str, GameOption] = {}  # dict of game_command -> GameOption

class LFGContext(object):

    def __init__(self, game_option: 'GameOption' = None,
                 host: discord.Member = None,
                 target_role: discord.Mentionable = None,
                 max_guests: int | None = None,
                 guests: set[discord.Member] | None = None,
                 users_to_notify: set[discord.Member] | None = None):
        super().__init__()
        self.game_option = game_option
        self.host = host
        self.target_role = target_role
        self.max_guests = max_guests
        self.guests = guests
        if (self.guests is None):
            self.guests = set()
        self.users_to_notify = users_to_notify
        if (self.users_to_notify is None):
            self.users_to_notify = set()

    @classmethod
    async def from_interaction(cls,
                               cog: 'Matchmaking', 
                               interaction: discord.Interaction):
        """Creates a new LFGContext from an interaction."""
        message = interaction.message
        if not message or not message.embeds:
            return cls()

        embed = message.embeds[0]
        guild = interaction.guild
        
        # Recover Game Option from title
        game_option = None
        title = embed.title or ""
        m = re.search(r"Looking for (?:an? )?(.+?) game$", title)
        if m:
            game_name = m.group(1).strip()
            guild_config = cog.get_guild_config(interaction.guild_id)
            for go in guild_config.games.values():
                if go.name == game_name:
                    game_option = go
                    break
        
        # Recover Host
        host = None
        for field in embed.fields:
            if field.name == "Host":
                h_id = utils.get_id_from_mention(field.value)
                if h_id:
                    host = guild.get_member(h_id)
                    if host is None:
                        try:
                            host = await guild.fetch_member(h_id)
                        except: pass
                break
        
        # Recover Target
        target_role = None
        for field in embed.fields:
            if field.name == "Target":
                t_id = utils.get_id_from_mention(field.value)
                if t_id:
                    target_role = (guild.get_role(t_id) or 
                                   guild.get_member(t_id) or 
                                   guild.get_channel(t_id))
                    if target_role is None:
                        try:
                            target_role = await guild.fetch_member(t_id)
                        except: pass
                break

        # Recover Guests & Max Guests
        guests = set()
        max_guests = None
        for field in embed.fields:
            if field.name.startswith("Guests"):
                m = re.search(r"Guests \((\d+)(?:/(\d+))?\)", field.name)
                if m:
                    if m.group(2):
                        max_guests = int(m.group(2))
                
                ids = re.findall(r"<@!?(\d+)>", field.value)
                for i in ids:
                    u_id = int(i)
                    member = guild.get_member(u_id)
                    if member is None:
                        try:
                            member = await guild.fetch_member(u_id)
                        except: pass
                    if member:
                        guests.add(member)
                break
        
        # Recover Users to Notify from the Subscribed field
        users_to_notify = set()
        for field in embed.fields:
            if field.name != "Subscribed":
                continue
            ids = re.findall(r"<@!?([0-9]+)>", field.value)
            for i in ids:
                u_id = int(i)
                member = guild.get_member(u_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(u_id)
                    except: pass
                if member:
                    users_to_notify.add(member)
            break

        context = cls(game_option=game_option,
                      host=host, target_role=target_role,
                      max_guests=max_guests, guests=guests,
                      users_to_notify=users_to_notify)

        return context
    

class LFGView(discord.ui.View):

    def __init__(self,
                 cog: 'Matchmaking' = None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label=LFG_JOIN_BUTTON_LABEL,
                       emoji=EMOJI_JOIN, style=discord.ButtonStyle.success,
                       custom_id=LFG_JOIN_CUSTOM_ID)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_join(interaction, context)

    @discord.ui.button(label=LFG_NOTIFY_BUTTON_LABEL,
                       emoji=EMOJI_NOTIFY, style=discord.ButtonStyle.danger,
                       custom_id=LFG_NOTIFY_CUSTOM_ID)
    async def notify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_notify(interaction, context)

    @discord.ui.button(label=LFG_CANCEL_BUTTON_LABEL,
                       emoji=EMOJI_CANCEL, style=discord.ButtonStyle.secondary,
                       custom_id=LFG_CANCEL_CUSTOM_ID)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_cancel(interaction, context)

    @discord.ui.button(label=LFG_START_BUTTON_LABEL,
                       emoji=EMOJI_START, style=discord.ButtonStyle.primary,
                       custom_id=LFG_START_CUSTOM_ID)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        context = await LFGContext.from_interaction(self.cog, interaction)
        await self.cog.process_start(interaction, context)


ModalCallback = Callable[
    [discord.Interaction, discord.ui.Modal, discord.ui.Select], Coroutine[Any, Any, None]
]
RenameModalCallback = Callable[
    [discord.Interaction, str], Coroutine[Any, Any, None]
]

class GameSettingsModal(discord.ui.Modal):

    description = discord.ui.TextInput(
        label="Description",
        placeholder="Provide details here...",
        style=discord.TextStyle.paragraph,
        max_length=200,
        required=False,
    )

    max_players = discord.ui.TextInput(
        label="Max number of players (2-100)",
        placeholder="Enter a whole number...",
        min_length=1,
        max_length=3,  # Prevents extremely large numbers
        required=False,
    )

    max_players_number = None  # This will hold the validated number of players

    def __init__(self, 
                 parent_select: discord.ui.Select | None = None,
                 title: str = "Game Settings",
                 on_confirm: ModalCallback | None = None,):
        super().__init__(title=title, timeout=300)
        self.parent_select = parent_select
        self.on_confirm = on_confirm

    async def on_submit(self, modal_interaction: discord.Interaction):
        # Validate that the input is a number
        if (self.max_players.value):
            try:
                value = int(self.max_players.value)
                if (value < 2 or value > 100):
                    raise ValueError
                self.max_players_number = value
            except ValueError:
                await modal_interaction.response.send_message(
                    "❌ **Invalid input:** Please enter a number from 2 to 100.",
                    ephemeral=True,
                )
                return

        if self.on_confirm:
            # Execute the custom callback passed during initialization
            await self.on_confirm(modal_interaction,
                                  self, self.parent_select)
        else:
            # Default fallback if no callback was provided
            await modal_interaction.response.send_message(
                f"Logged **{self.title}** request:\n> {self.description.value}",
                ephemeral=True,
            )

class ThreadRenameModal(discord.ui.Modal):

    title_input = discord.ui.TextInput(
        label="Thread title",
        placeholder="Enter a new thread title...",
        max_length=100,
        required=True,
    )

    def __init__(self, on_confirm: RenameModalCallback):
        super().__init__(title="Rename Thread", timeout=300)
        self.on_confirm = on_confirm

    async def on_submit(self, interaction: discord.Interaction):
        await self.on_confirm(interaction, self.title_input.value)

class Matchmaking(commands.Cog):

    def __init__(self, bot: commands.Bot,
                 config : configparser.ConfigParser = None):
        self.bot = bot
        self.guilds : dict[int, GuildGamesConfig] = {} # dict of guild_id -> GuildGamesConfig
        self.default_guild_config = GuildGamesConfig(None)
        self._load_guild_config(self.default_guild_config, config, common.CONFIG_DEFAULT)
        for guild in config.sections():
            guild_id = config.getint(guild, common.CONFIG_ID, fallback=None)
            if (guild_id is None):
                continue
            guild_config = GuildGamesConfig(guild_id)
            self._load_guild_config(guild_config, config, guild)
            self.guilds[guild_id] = guild_config

        self.custom_emoji_re = re.compile(r"<:[\w]+:[\d]+>")

    def _load_guild_config(self, guild_config: GuildGamesConfig,
                           config: configparser.ConfigParser,
                           section: str):
        configdict = {}
        for arg in CONFIG_GAMES_ARGS:
            configdict[arg] = utils.split_config_list(config.get(section, arg, fallback=None))
        for game in configdict[CONFIG_GAMES_COMMANDS]:
            index = configdict[CONFIG_GAMES_COMMANDS].index(game)
            game_option = GameOption(
                name=utils.safe_list_get(configdict[CONFIG_GAMES_NAMES], index, ""),
                command=game,
                role=utils.safe_list_get(configdict[CONFIG_GAMES_ROLES], index, ""),
                icon=utils.safe_list_get(configdict[CONFIG_GAMES_ICONS], index, ""),
                color=utils.safe_list_get(configdict[CONFIG_GAMES_COLORS], index, ""),
                forum=utils.safe_list_get(configdict[CONFIG_GAMES_FORUMS], index, None),
                tag=utils.safe_list_get(configdict[CONFIG_GAMES_TAGS], index, None),
                visibility=utils.safe_list_get(configdict[CONFIG_GAMES_VISIBILITY], index, None),
                message=utils.safe_list_get(configdict[CONFIG_GAMES_MESSAGES], index, None),
                registration_api=utils.safe_list_get(configdict[CONFIG_GAMES_REGISTRATION_API], index, None),
                match_api=utils.safe_list_get(configdict[CONFIG_GAMES_MATCH_API], index, None),
                match_url=utils.safe_list_get(configdict[CONFIG_GAMES_MATCH_URL], index, None),
                api_token_env_var=utils.safe_list_get(configdict[CONFIG_GAMES_API_TOKEN_ENV_VARS], index, None),
                website_url=utils.safe_list_get(configdict[CONFIG_GAMES_WEBSITE_URL], index, None),
                registration_url=utils.safe_list_get(configdict[CONFIG_GAMES_REGISTRATION_URL], index, None),
                profile_url=utils.safe_list_get(configdict[CONFIG_GAMES_PROFILE_URL], index, None),
                default_max_guests=self.parse_default_max_guests(
                    utils.safe_list_get(configdict[CONFIG_GAMES_MAX_PLAYERS], index, None)
                )
            )
            guild_config.games[game] = game_option

    def get_guild_config(self, guild_id: int) -> GuildGamesConfig:
        return self.guilds.get(guild_id, self.default_guild_config)

    @staticmethod
    def parse_default_max_guests(max_players : str | None) -> int | None:
        if (not max_players):
            return None
        try:
            parsed_value = int(max_players)
        except ValueError:
            return None
        return parsed_value - 1 if 2 <= parsed_value <= 100 else None

    async def game_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild_config = self.get_guild_config(interaction.guild_id)

        return [
            app_commands.Choice(name=game.name, value=game.command)
            for game in guild_config.games.values()
            if current.lower() in game.command.lower()
        ][:25]

    async def send_help(self, interaction: discord.Interaction, topic: str):
        if (topic == LFG_COMMAND):
            message = (
                "# Help: /lfg\n"
                "Create a looking-for-group post using either the guided "
                "menus or direct command arguments.\n"
                "## Guided mode\n"
                "Use `/lfg` without arguments to choose a game and enter "
                "the settings through the menus.\n"
                "Inside a game thread created by the bot, this opens instead a modal to rename the thread.\n"
                "## Direct mode\n"
                "`/lfg game:<game> [description:<text>] "
                "[max_players:<number>]`\n"
                "### game\n"
                "The game/role to ping for this LFG post.\n"
                "### description\n"
                "Optional description for the game.\n"
                "Inside a game thread created by the bot, provide only `description` to rename the thread.\n"
                "### max_players\n"
                "Optional maximum number of players (including host) (2-100).\n"
                "The LFG will automatically close when this number is reached.\n"
                "Some games may have a default maximum number of players, which will be used if this argument is not provided.\n"
            )
            guild_config = self.get_guild_config(interaction.guild_id)
            games = list(guild_config.games.values())
            if (games):
                alignment = len(max((game.command for game in games), key=len))
                game_lines = []
                for game in games:
                    line = f"- `{game.command.ljust(alignment)}`"
                    if (game.name):
                        line += (
                            " - "
                            f"{utils.indefinite_article(game.name)} **{game.name}** game"
                        )
                    settings = []
                    if (game.role):
                        settings.append(f"role to ping: {game.role}")
                    if (game.forum):
                        forum = game.forum
                        if (forum.isdigit()):
                            forum = f"<#{forum}>"
                        settings.append(f"target forum: {forum}")
                    if (game.default_max_guests is not None):
                        settings.append(f"players: {game.default_max_guests + 1}")
                    if (settings):
                        line += " (" + ", ".join(settings) + ")"
                    line += "."
                    game_lines.append(line)
                message += "## Available games\n" + "\n".join(game_lines)
            else:
                message += "## Available games\nNo games are configured for this server."
            await interaction.response.send_message(message, ephemeral=True)
        else:
            message = (
                f"# Help: /{topic}\n"
                "No detailed help is available for this command yet."
            )
            await interaction.response.send_message(message, ephemeral=True)

    async def process_game_selection(self,
                                     interaction: discord.Interaction,
                                     command_interaction: discord.Interaction,
                                     select: discord.ui.Select
    ):
        modal = GameSettingsModal(
            parent_select=select,
            on_confirm=self.process_game_settings)

        await interaction.response.send_modal(modal)

        await command_interaction.delete_original_response()

    async def process_game_settings(self,
                                       interaction: discord.Interaction,
                                       modal: discord.ui.Modal,
                                       select: discord.ui.Select
    ):
        await interaction.response.defer(ephemeral=True)

        game_command = select.values[0]
        game_option = self.get_guild_config(interaction.guild_id).games.get(game_command)

        max_players = modal.max_players_number
        if (max_players is None):
            max_guests = game_option.default_max_guests
        else:
            max_guests = max_players - 1
        await self.create_lfg(interaction, game_option, modal.description.value, max_guests)

    async def process_join(self, interaction: discord.Interaction, context: LFGContext):
        if (context.host == interaction.user):
            await interaction.response.send_message(
                f"You are the host of this game.", ephemeral=True
            )
            return
        
        is_joining = interaction.user not in context.guests
        if (is_joining and context.max_guests is not None and len(context.guests) >= context.max_guests):
            await interaction.response.send_message(
                f"Sorry, this game is already full.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if (is_joining):
            context.guests.add(interaction.user)
        else:
            context.guests.remove(interaction.user)

        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if (embed is not None):
            # Guest list
            guests_string = ""
            for guest in context.guests:
                previous_size = len(guests_string)
                new_guest = ""
                if (previous_size):
                    new_guest += ", "
                new_guest += guest.display_name + " (" + guest.mention + ")"
                if (previous_size + len(new_guest) + len(GUESTS_OVER_LIMIT) <= 1024):
                    guests_string += new_guest
                elif (previous_size):
                    guests_string += GUESTS_OVER_LIMIT
                    break
            # Update the embed with the new guest list
            embed.clear_fields()
            if (context.target_role):
                embed.add_field(name="Target", value=context.target_role.mention, inline=True)
            if (context.host):
                embed.add_field(name="Host", value=context.host.mention, inline=True)
            nb_guests = len(context.guests)
            if (nb_guests or context.max_guests is not None):
                field_name = f"Guests ({nb_guests}"
                if (context.max_guests is not None):
                    field_name += f"/{context.max_guests}"
                field_name += ")"
                embed.add_field(name=field_name, value=guests_string, inline=False)
            if (context.users_to_notify):
                sub_mentions = ",".join(
                    u.mention for u in sorted(list(context.users_to_notify), key=lambda x: x.id)
                )
                embed.add_field(name="Subscribed", value=sub_mentions, inline=False)
            try:
                await message.edit(embed=embed)
            except Exception as error:
                print(error)

        if (is_joining):
            # Notify users
            await self.notify_players(interaction.channel, context.host, interaction.user, context.users_to_notify)
            pass

        await interaction.followup.send(
            content=(f"You have joined the game!" if is_joining
                     else f"You have left the game!"), ephemeral=True
        )
        
        if (is_joining and context.max_guests is not None and len(context.guests) >= context.max_guests):
            # factorize game start from process_start to allow for automatic start when max guests reached
            await self.start_game(interaction, context)

    async def process_notify(self, interaction: discord.Interaction, context: LFGContext):
        await interaction.response.defer(ephemeral=True)

        is_subscribing = interaction.user not in context.users_to_notify
        if (is_subscribing):
            context.users_to_notify.add(interaction.user)
        else:
            context.users_to_notify.remove(interaction.user)

        # Update message to persist users_to_notify in a separate field
        message = interaction.message
        embed = message.embeds[0]
        subscribed_field_index = next(
            (index for index, field in enumerate(embed.fields)
             if field.name == "Subscribed"),
            None,
        )
        if (subscribed_field_index is not None):
            embed.remove_field(subscribed_field_index)
        if context.users_to_notify:
            sub_mentions = ",".join(
                u.mention for u in sorted(list(context.users_to_notify), key=lambda x: x.id)
            )
            embed.add_field(name="Subscribed", value=sub_mentions, inline=False)
        try:
            await message.edit(embed=embed)
        except Exception as error:
            print(error)

        await interaction.followup.send(
            content=(f"You will be notified when someone joins the game!" if is_subscribing
                     else f"You will no longer be notified when someone joins the game!"), ephemeral=True
        )

    async def process_cancel(self, interaction: discord.Interaction, context: LFGContext):
        await interaction.response.defer(ephemeral=True)

        if (context.host != interaction.user):
            await interaction.followup.send(
                content=f"Only the host can cancel the game.", ephemeral=True
            )
            return

        await self.close_game(interaction,
                              emoji=EMOJI_CANCEL,
                              footer_text="Game cancelled. Sorry!")
        
        await interaction.followup.send(
            content=f"The game has been canceled.", ephemeral=True
        )

    async def process_start(self, interaction: discord.Interaction, context: LFGContext):
        await interaction.response.defer(ephemeral=True)

        if (context.host != interaction.user):
            await interaction.followup.send(
                content=f"Only the host can start the game.", ephemeral=True
            )
            return

        await self.start_game(interaction, context)

    async def start_game(self, interaction: discord.Interaction, context: LFGContext):
        await self.close_game(interaction,
                              emoji=EMOJI_START,
                              footer_text="Game already started. Sorry!")

        await self.create_game_thread(interaction, context)

        await interaction.followup.send(
            content=f"The game has started!", ephemeral=True
        )

    async def close_game(self, interaction: discord.Interaction,
                         emoji: str = EMOJI_START,
                         footer_text: str = "Game closed/full. Sorry!"):
        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if (embed is not None):
            emoji_url = utils.get_default_emoji_url(emoji)
            embed.set_footer(text=footer_text, icon_url=emoji_url)
            try:
                await message.edit(embed=embed)
            except Exception as error:
                print(error)

        await self.remove_view(interaction)

    async def create_lfg(self, interaction: discord.Interaction,
                         game_option: GameOption,
                         description: str,
                         max_guests: int | None):
        # Create an LFG post
        # Embed + buttons to interact

        embed = discord.Embed(description=description)
        embed.set_footer(text=LFG_FOOTER_HELP)
        
        if (len(game_option.role)):
            embed.add_field(name="Target", value=game_option.role, inline=True)
        
        host = interaction.user
        field_text = host.mention
        embed.add_field(name="Host", value=field_text, inline=True)

        if (max_guests is not None):
            embed.add_field(name=f"Guests (0/{max_guests})", value="", inline=False)
        
        author_avatar = common.DEFAULT_AVATAR_URL
        display_avatar = host.display_avatar
        if (display_avatar is not None):
            author_avatar = display_avatar.url
        embed.set_author(name=host.display_name,
                         icon_url=author_avatar)
        
        embed.title = "Looking for " 
        embed.title += utils.indefinite_article(game_option.name)
        embed.title += " " + game_option.name + " game"

        gameIcon = game_option.icon
        if (not(len(gameIcon))):
            gameIcon = common.DEFAULT_AVATAR_URL
        embed.set_thumbnail(url=gameIcon)

        gameColor = game_option.color
        if (not(len(gameColor))):
            gameColor = host.colour
        embed.colour = gameColor

        role_id = None
        if (len(game_option.role)):
            role_id = utils.get_id_from_mention(game_option.role)
        target_role = None
        if (role_id is not None):
            target_role = interaction.guild.get_role(role_id)
            if (target_role is None):
                target_role = interaction.guild.get_member(role_id)
            if (target_role is None):
                target_role = interaction.guild.get_channel(role_id)

        # View for the buttons
        view = LFGView(cog=self)
        
        try:
            await interaction.followup.send(content=game_option.role, embed=embed, view=view, ephemeral=False)
        except Exception as error:
            print(error)
    
    async def notify_players(self, channel, host, new_player, users_to_notify):
        for user_to_notify in users_to_notify:
            if (user_to_notify == new_player):
                continue

            message_to_send = "A new player (" + new_player.display_name + ")"
            message_to_send += " has joined your game"
            message_to_send += " in the LFG channel "
            message_to_send += channel.mention + ".\n"
            if (user_to_notify == host):
                message_to_send += "When the game is full,"
                message_to_send += " you can start the thread using "
                message_to_send += EMOJI_START + ", which will ping"
                message_to_send += " all the players. GLHF!"
            else:
                message_to_send += "When the game thread starts,"
                message_to_send += " you will be pinged there. GLHF!"
            try:
                await user_to_notify.send(message_to_send)
            except Exception as e:
                print(e)
                print("Failed to DM " + user_to_notify.display_name)

    async def remove_view(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            await interaction.message.edit(view=None)
        else:
            await interaction.response.edit_message(view=None)


    async def create_game_thread(self, interaction: discord.Interaction, context: LFGContext):
        message = interaction.message
        channel = interaction.channel
        host = context.host
        guests = context.guests
        game_option = context.game_option
        embed = message.embeds[0] if message.embeds else None

        # Create thread
        # 3 cases: a) Do nothing if this message already has a thread
        #          b) Create thread in a (forum) channel if available
        #          c) Create thread under this message otherwise
         
        thread_channel = channel
        parent_message = message
        thread_in_forum = False
        thread_pings = host.mention
        for guest in guests:
            if (len(thread_pings)):
                thread_pings += ", "
            thread_pings += guest.mention
        thread_message = thread_pings + ", "
        thread_message += "your game can start! GLHF!"
        game_message = game_option.message
        if (len(game_message)):
            thread_message += " " + game_message
        thread_embed = None
        
        # Thread title = embed description without custom emojis
        thread_title = utils.clean_thread_title(embed.description, self.custom_emoji_re)
        if (thread_title is None or not(len(thread_title))):
            thread_title = embed.title
        if (thread_title is None or not(len(thread_title))):
            thread_title = "Game thread"
        thread_visibility = True
        thread_tag = None
        
        keywords = {}
        keywords['name'] = thread_title
        
        forum_id = ""
        if (game_option.forum):
            forum_id = game_option.forum
        forum = None
        tag_name = ""
        if (game_option.tag):
            tag_name = game_option.tag
        if (len(forum_id)):
            forum = self.bot.get_channel(int(forum_id))
        if (forum is not None):
            thread_in_forum = True
            thread_channel = forum
            if (len(tag_name)):
                for forum_tag in forum.available_tags:
                    if (forum_tag.name == tag_name):
                        thread_tag = forum_tag
        if (game_option.visibility):
            visibility = game_option.visibility
            if (len(visibility) and int(visibility) == 0):
                thread_visibility = False
        
        thread_has_parent = not(thread_in_forum) and thread_visibility
        if (not(thread_has_parent)):
            thread_embed = embed.copy()
            thread_embed.url = message.jump_url
            thread_embed.remove_footer()
        if (thread_in_forum):
            if (thread_tag is not None):
                keywords['applied_tags'] = [thread_tag]
            keywords['content'] = thread_message
            keywords['embed'] = thread_embed
        if(thread_has_parent):
            keywords['message'] = parent_message
        if (not(thread_visibility)):
            keywords['type'] = discord.ChannelType.private_thread

        gameName = None
        if (game_option.name):
            gameName = game_option.name
        registration_api_url = None
        if (game_option.registration_api):
            registration_api_url = game_option.registration_api
        match_api_url = None
        if (game_option.match_api):
            match_api_url = game_option.match_api
        match_url = None
        if (game_option.match_url):
            match_url = game_option.match_url
        auth_token = None
        if (game_option.api_token_env_var):
            token_env_var = game_option.api_token_env_var
            if (token_env_var):
                auth_token = os.getenv(token_env_var)
        website_url = None
        if (game_option.website_url):
            website_url = game_option.website_url
        registration_url = None
        if (game_option.registration_url):
            registration_url = game_option.registration_url
        profile_url = None
        if (game_option.profile_url):
            profile_url = game_option.profile_url

        try:
            thread = await thread_channel.create_thread(**keywords)
            if (thread_in_forum):
                thread, _ = thread
            if (thread is not None):
                if (not(thread_in_forum)):
                    await thread.send(content=thread_message, embed=thread_embed)
                if (thread_in_forum):
                    embed.url = thread.jump_url
                else:
                    embed.url = message.jump_url
                await message.edit(embed=embed)

                verified_users = []
                if registration_api_url:
                    try:
                        users = [host] + list(guests)
                        verified_users = await self.check_registration(
                            thread, users, registration_api_url,
                            gameName, auth_token,
                            website_url, registration_url, profile_url)
                    except Exception as error:
                        print(f"League player registration check failed: {error}")
                if match_api_url:
                    try:
                        await self.register_match(
                            thread, match_api_url, match_url,
                            auth_token, thread_title, gameName,
                            verified_users)
                    except Exception as error:
                        print(f"League match registration request failed: {error}")
        except Exception as e:
            print(e)

    async def register_match(self, thread, match_api_url, match_url,
                             auth_token, title, website_name, verified_users):
        headers = {"Content-Type": "application/json"}
        if (auth_token):
            headers["Authorization"] = f"Token {auth_token}"

        payload = {
            "title": title,
            "table_talk_url": thread.jump_url,
        }

        if (verified_users):
            payload["participants"] = [
                {"discord_username": user.name}
                for user in verified_users
            ]

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(match_api_url, json=payload) as response:
                if response.status not in (200, 201):
                    response_text = await response.text()
                    print(f"League match registration failed ({response.status}): {response_text}")
                    return None

                match = await response.json()
                match_id = match.get("id")
                if (match_id is None):
                    return None
                confirmation_message = f"Game preregistered on {website_name}"
                if (match_url):
                    preregistered_match_url = f"{match_url.rstrip('/')}/{match_id}/"
                    confirmation_message += f": {preregistered_match_url}"
                else:
                    confirmation_message += " with ID: " + str(match_id)
                await thread.send(content=confirmation_message)

    async def check_registration(self, thread, users, api_url,
                                 website_name=None, auth_token=None,
                                 website_url=None, registration_url=None, profile_url=None):
        if (not(api_url) or not(users)):
            return []

        unregistered_users = []
        verified_users = []
        headers = None
        if (auth_token):
            headers={"Authorization": f"Token {auth_token}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            for user in users:
                if user.id == self.bot.user.id:
                    # Skip the bot's user itself
                    continue
                async with session.get(api_url + user.name + "/") as response:
                    if response.status != 200:
                        unregistered_users.append(user)
                    else:
                        verified_users.append(user)

        if (not(unregistered_users)):
            return verified_users

        message = "Welcome"
        for user in unregistered_users:
            message += f", {user.mention}"
        message += "! I couldn't find your registration"
        if (website_name):
            message += " on "
            if (website_url):
                message += f"[{website_name}]({website_url})"
            else:
                message += website_name
            message += " website"
        message += ". Please "
        if (registration_url):
            message += f"[register]({registration_url})"
        else:
            message += "register"
        message += " and/or double check" \
                   " that your "
        if (profile_url):
            message += f"[profile]({profile_url})"
        else:
            message += "profile"
        message += " has the correct Discord username. "
        
        await thread.send(content=message, suppress_embeds=True)
        return verified_users

    async def check_thread_rename_permission(self,
                                             interaction: discord.Interaction
                                             ) -> bool | None:
        channel = interaction.channel
        if (channel is None or channel.type not in THREAD_TYPES):
            return None

        if (channel.owner_id != self.bot.user.id):
            await interaction.response.send_message(
                "This thread cannot be renamed with `/lfg`.", ephemeral=True
            )
            return False

        try:
            starter_message = await channel.fetch_message(channel.id)
        except discord.HTTPException:
            starter_message = None

        host_id = None
        if (starter_message is not None and starter_message.embeds):
            for field in starter_message.embeds[0].fields:
                if (field.name == "Host"):
                    host_id = utils.get_id_from_mention(field.value)
                    break

        if (host_id != interaction.user.id):
            await interaction.response.send_message(
                "Only the host can rename this thread.", ephemeral=True
            )
            return False

        return True

    async def rename_thread(self, interaction: discord.Interaction,
                                 title: str):
        channel = interaction.channel
        new_name = utils.clean_thread_title(title, self.custom_emoji_re)
        if (not new_name):
            await interaction.response.send_message(
                "Thread title cannot be empty.", ephemeral=True
            )
            return

        await channel.edit(name=new_name)
        await interaction.response.send_message(
            f"Thread renamed to **{new_name}**.", ephemeral=True
        )

    async def rename_thread_direct(self, interaction: discord.Interaction,
                                   title: str | None):
        can_rename = await self.check_thread_rename_permission(interaction)
        if (can_rename is None):
            return False
        if (not can_rename):
            return True

        if (title is None or not title.strip()):
            await interaction.response.send_modal(
                ThreadRenameModal(self.rename_thread_modal)
            )
            return True

        await self.rename_thread(interaction, title)
        return True

    async def rename_thread_modal(self, interaction: discord.Interaction,
                                  title: str):
        if (not await self.check_thread_rename_permission(interaction)):
            return

        await self.rename_thread(interaction, title)


    @app_commands.command(
        name=LFG_COMMAND, description=LFG_DESCRIPTION
    )
    @app_commands.describe(
        game="The game/role to ping for this LFG post.",
        description="Optional description for the game.",
        max_players="Optional maximum number of players (including host) (2-100).",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def lfg(self, interaction: discord.Interaction,
                  game: str | None = None,
                  description: str | None = None,
                  max_players: app_commands.Range[int, 2, 100] | None = None,
                  ):
        if (await self.rename_thread_direct(interaction, description)):
            return

        if (game is not None):
            guild_config = self.get_guild_config(interaction.guild_id)
            game_option = guild_config.games.get(game)
            if (game_option is None):
                # If discord unfortunately sent the name instead of value during autocomplete...
                # We need to check the name too.
                for go in guild_config.games.values():
                    if go.name == game:
                        game_option = go
                        break
            if (game_option is None):
                await interaction.response.send_message(
                    f"`{game}` is not a configured game for this server.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer()
            if (max_players is None):
                max_guests = game_option.default_max_guests
            else:
                max_guests = max_players - 1
            await self.create_lfg(interaction, game_option, description or "", max_guests)
            return

        if (description is not None or max_players is not None):
            await interaction.response.send_message(
                "The `game` argument is required when using direct settings.",
                ephemeral=True,
            )
            return

        choices = [ (game_option.name, game_option.command)
                    for game_option in self.get_guild_config(interaction.guild_id).games.values() ]

        view = DynamicSelectView(
            choices=choices, 
            command_interaction=interaction,
            on_select=self.process_game_selection,
            placeholder="Select a game option...",
            timeout=300
        )

        await interaction.response.send_message(
            view=view, ephemeral=True
        )

async def setup(bot: commands.Bot):
    config = configparser.ConfigParser()
    config.read('config/games.ini')
    cog = Matchmaking(bot=bot, config=config)
    await bot.add_cog(cog)
    bot.add_view(LFGView(cog=cog))