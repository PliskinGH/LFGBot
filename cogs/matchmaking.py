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
    CONFIG_GAMES_PROFILE_URL
]
EMOJI_JOIN = "👍"
EMOJI_NOTIFY = "🔔"
EMOJI_CANCEL = "❌"
EMOJI_START = "✅"
EMOJIS_VALID = [EMOJI_JOIN, EMOJI_NOTIFY, EMOJI_CANCEL, EMOJI_START]
EMOJIS_CLOSE = [EMOJI_CANCEL, EMOJI_START]

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
                 registration_url, profile_url):
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

class GuildGamesConfig(object):

    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.games = {}  # dict of game_command -> GameOption

class LFGView(DynamicButtonView):

    def __init__(self, cog : Matchmaking,
                 game_option : GameOption = None,
                 host : discord.Member = None,
                 target_role : discord.Mentionable = None,
                 max_guests : int | None = None):
        # Tuple format: (label, emoji, callback, optional_style)
        button_configs = [
            (
                "Join",
                EMOJI_JOIN,
                cog.process_join,
                discord.ButtonStyle.success,
            ),
            (
                "Notify me",
                EMOJI_NOTIFY,
                cog.process_notify,
                discord.ButtonStyle.danger,
            ),
            ("Cancel", EMOJI_CANCEL, cog.process_cancel, discord.ButtonStyle.secondary),
            ("Start", EMOJI_START, cog.process_start, discord.ButtonStyle.primary),
        ]
        super().__init__(timeout=None, buttons=button_configs)
        self.game_option = game_option
        self.host = host
        self.target_role = target_role
        self.guests = set()
        self.users_to_notify = set()
        self.max_guests = max_guests  # Maximum number of guests allowed in the game


ModalCallback = Callable[
    [discord.Interaction, discord.ui.Modal, discord.ui.Select], Coroutine[Any, Any, None]
]

class GameSettingsModal(discord.ui.Modal):

    description = discord.ui.TextInput(
        label="Description",
        placeholder="Provide details here...",
        style=discord.TextStyle.paragraph,
        max_length=200,
        required=False,
    )

    max_guests = discord.ui.TextInput(
        label="Max number of guests (1-100)",
        placeholder="Enter a whole number...",
        min_length=1,
        max_length=3,  # Prevents extremely large numbers
        required=False,
    )

    max_guests_number = None  # This will hold the validated number of guests

    def __init__(self, 
                 parent_select: discord.ui.Select | None = None,
                 title: str = "Game Settings",
                 on_confirm: ModalCallback | None = None,):
        super().__init__(title=title, timeout=300)
        self.parent_select = parent_select
        self.on_confirm = on_confirm

    async def on_submit(self, modal_interaction: discord.Interaction):
        # Validate that the input is a number
        if (self.max_guests.value):
            try:
                value = int(self.max_guests.value)
                self.max_guests_number = value
            except ValueError:
                await modal_interaction.response.send_message(
                    "❌ **Invalid input:** Please enter numbers only.",
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

class Matchmaking(commands.Cog):

    def __init__(self, bot: commands.Bot,
                 config : configparser.ConfigParser = None):
        self.bot = bot
        self.guilds = {} # dict of guild_id -> GuildGamesConfig
        for guild in config.sections():
            guild_id = config.getint(guild, common.CONFIG_ID, fallback=None)
            if (guild_id is None):
                continue
            guild_config = GuildGamesConfig(guild_id)
            configdict = {}
            for arg in CONFIG_GAMES_ARGS:
                configdict[arg] = utils.split_config_list(config.get(guild, arg, fallback=None))
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
                    profile_url=utils.safe_list_get(configdict[CONFIG_GAMES_PROFILE_URL], index, None)
                )
                guild_config.games[game] = game_option
            self.guilds[guild_id] = guild_config

        self.custom_emoji_re = re.compile(r"<:[\w]+:[\d]+>")

    async def game_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild_config = self.guilds.get(interaction.guild_id)
        if (guild_config is None):
            return []

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
                "## Direct mode\n"
                "`/lfg game:<game> [description:<text>] "
                "[max_guests:<number>]`\n"
                "### game\n"
                "The game/role to ping for this LFG post.\n"
                "### description\n"
                "Optional description for the game.\n"
                "### max_guests\n"
                "Optional maximum number of guests (1-100). The LFG will "
                "automatically close when this number is reached.\n"
            )
            guild_config = self.guilds.get(interaction.guild_id)
            games = list(guild_config.games.values()) if guild_config else []
            if (games):
                alignment = len(max((game.command for game in games), key=len))
                game_lines = []
                for game in games:
                    line = f"- `{game.command.ljust(alignment)}`"
                    if (game.name):
                        line += (
                            " - "
                            f"{utils.indefinite_article(game.name)} {game.name} game."
                        )
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
        game_option = self.guilds[interaction.guild_id].games.get(game_command)

        await self.create_lfg(interaction, game_option, modal.description.value, modal.max_guests_number)

    async def process_join(self, interaction: discord.Interaction, view: discord.ui.View):
        if (view.is_finished()):
            await interaction.response.send_message(
                f"This game is no longer active.", ephemeral=True
            )
            return

        if (view.host == interaction.user):
            await interaction.response.send_message(
                f"You are the host of this game.", ephemeral=True
            )
            return
        
        if (view.max_guests is not None and len(view.guests) >= view.max_guests):
            await interaction.response.send_message(
                f"Sorry, this game is already full.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        view.guests.add(interaction.user)

        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if (embed is not None):
            # Guest list
            guests_string = ""
            for guest in view.guests:
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
            if (view.target_role):
                embed.add_field(name="Target", value=view.target_role.mention, inline=True)
            if (view.host):
                embed.add_field(name="Host", value=view.host.mention, inline=True)
            nb_guests = len(view.guests)
            if (nb_guests or view.max_guests is not None):
                field_name = f"Guests ({nb_guests}"
                if (view.max_guests is not None):
                    field_name += f"/{view.max_guests}"
                field_name += ")"
                embed.add_field(name=field_name, value=guests_string, inline=False)
            try:
                await message.edit(embed=embed)
            except Exception as error:
                print(error)

        if (interaction.user in view.guests):
            # Notify users
            await self.notify_players(interaction.channel, view.host, interaction.user, view.users_to_notify)
            pass

        await interaction.followup.send(
            content=f"You have joined the game!", ephemeral=True
        )
        
        if (view.max_guests is not None and len(view.guests) >= view.max_guests):
            # factorize game start from process_start to allow for automatic start when max guests reached
            await self.start_game(interaction, view)

    async def process_notify(self, interaction: discord.Interaction, view: discord.ui.View):
        await interaction.response.defer(ephemeral=True)

        view.users_to_notify.add(interaction.user)

        await interaction.followup.send(
            content=f"You will be notified when someone joins the game!", ephemeral=True
        )

    async def process_cancel(self, interaction: discord.Interaction, view: discord.ui.View):
        await interaction.response.defer(ephemeral=True)

        if (view.host != interaction.user):
            await interaction.followup.send(
                content=f"Only the host can cancel the game.", ephemeral=True
            )
            return

        await self.close_game(interaction, view, emoji=EMOJI_CANCEL)
        
        await interaction.followup.send(
            content=f"The game has been canceled.", ephemeral=True
        )

    async def process_start(self, interaction: discord.Interaction, view: discord.ui.View):
        await interaction.response.defer(ephemeral=True)

        if (view.host != interaction.user):
            await interaction.followup.send(
                content=f"Only the host can start the game.", ephemeral=True
            )
            return

        await self.start_game(interaction, view)

    async def start_game(self, interaction: discord.Interaction, view: discord.ui.View):
        await self.close_game(interaction, view, emoji=EMOJI_START)

        await self.create_game_thread(interaction, view)

        await interaction.followup.send(
            content=f"The game has started!", ephemeral=True
        )

    async def close_game(self, interaction: discord.Interaction, view: discord.ui.View, emoji: str = EMOJI_START):
        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if (embed is not None):
            emoji_url = utils.get_default_emoji_url(emoji)
            embed.set_footer(text="Game closed/full. Sorry!", icon_url=emoji_url)
            try:
                await message.edit(embed=embed)
            except Exception as error:
                print(error)

        await self.disable_view(interaction, view)

    async def create_lfg(self, interaction: discord.Interaction,
                         game_option: GameOption,
                         description: str,
                         max_guests: int | None):
        # Create an LFG post
        # Embed + buttons to interact

        embed = discord.Embed(description=description)
        embed.set_footer(text="For discussion about this game, please use a thread.\nIt will be created for you when you close the game.")
        
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
        view = LFGView(cog=self, 
                       game_option=game_option, 
                       host=host,
                       target_role=target_role,
                       max_guests=max_guests)
        
        try:
            await interaction.followup.send(content=game_option.role, embed=embed, view=view, ephemeral=False)
        except Exception as error:
            print(error)
    
    async def notify_players(self, channel, host, new_player, users_to_notify):
        for user_to_notify in users_to_notify:
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

    async def disable_view(self, interaction: discord.Interaction, view: discord.ui.View):
        # Disable all buttons in the view
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        view.stop()  # Stop listening for interactions

        await interaction.edit_original_response(view=view)  # Update the original message with the disabled view


    async def create_game_thread(self, interaction: discord.Interaction, view: LFGView):
        message = interaction.message
        channel = interaction.channel
        host = view.host
        guests = view.guests
        game_option = view.game_option
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


    @app_commands.command(
        name=LFG_COMMAND, description=LFG_DESCRIPTION
    )
    @app_commands.describe(
        game="The game/role to ping for this LFG post.",
        description="Optional description for the game.",
        max_guests="Optional maximum number of guests (1-100). The LFG will automatically close when this number is reached.",
    )
    @app_commands.autocomplete(game=game_autocomplete)
    async def lfg(self, interaction: discord.Interaction,
                  game: str | None = None,
                  description: str | None = None,
                  max_guests: app_commands.Range[int, 1, 100] | None = None,
                  ):
        if (game is not None):
            guild_config = self.guilds.get(interaction.guild_id)
            game_option = guild_config.games.get(game) if guild_config else None
            if (game_option is None):
                await interaction.response.send_message(
                    f"`{game}` is not a configured game for this server.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer()
            await self.create_lfg(interaction, game_option, description or "", max_guests)
            return

        if (description is not None or max_guests is not None):
            await interaction.response.send_message(
                "The `game` argument is required when using direct settings.",
                ephemeral=True,
            )
            return

        choices = [ (game_option.name, game_option.command)
                    for game_option in self.guilds[interaction.guild_id].games.values() ]

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
    await bot.add_cog(Matchmaking(bot=bot, config=config))