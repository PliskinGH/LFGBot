import configparser
import datetime
import re
from typing import Optional, TypedDict

import discord.abc
from discord import TextChannel, Thread, Message, ForumChannel, app_commands
from discord.ext import commands

from common import utils

CONFIG_GAMES_COMMANDS = "GamesCommands"
CONFIG_GAMES_FORUMS = "GamesForums"

MYGAMES_COMMAND = "mygames"
HOST_MENTION_REGEX = re.compile(r"<@(\d+)>")
GUESTS_MENTION_REGEX = re.compile(r".+ \(<@(\d+)>\)")


class MyGames(commands.Cog):
    """
    Displays a list of games that you were involved in the past thirty days. This command should be run inside
    the channel that contains all the game threads (eg. '#rdl-lfg')
    """

    def __init__(self, bot, config=None):
        self.bot = bot
        self.config = config

    @app_commands.command(name=MYGAMES_COMMAND, description="List games you're involved in the past thirty days")
    async def mygames(self, interaction: discord.Interaction, game: str | None):
        """
        Displays a list of games/threads that you are involved in, in the past 30 days.
        """
        await interaction.response.defer(ephemeral=True)

        guild: discord.Guild | None = interaction.guild

        if guild is None:
            return

        author: discord.User | discord.Member = interaction.user

        (games, forums) = (self.get_configured_games(guild.id, CONFIG_GAMES_COMMANDS, CONFIG_GAMES_FORUMS))

        if game is None:
            await interaction.followup.send(content=F"""
Select a game to look at your historical games. eg. /mygames rootdigleague
Available games are [{", ".join(games)}]
            """, ephemeral=True)
            return

        selected_game = game

        try:
            game_index = games.index(selected_game)
        except ValueError:
            await interaction.followup.send(content=F"""
The game you specified doesn't exist.

Select a game to look at your historical games. eg. /mygames rootdigleague
Available games are [{", ".join(games)}]
            """, ephemeral=True)
            return

        forum: str = forums[game_index]

        if forum is None or forum.strip() == "":
            await interaction.followup.send(content=F"""
This game does not support game threads. Select a different game.
""", ephemeral=True)
            return

        channel = await guild.fetch_channel(int(forum))

        if not isinstance(channel, ForumChannel):
            await interaction.followup.send(content=F"""
This game does not use a forum channel. Select a different game.
            """, ephemeral=True)
            return

        games: list[Game] = []

        now = datetime.datetime.now(datetime.timezone.utc)
        after = now - datetime.timedelta(days=30)

        for thread in channel.threads:
            async for message in thread.history(limit=1, oldest_first=True):
                if message is not None and message.created_at >= after:
                    game = Game(message)
                    if author in game:
                        games.append(game)

        async for thread in channel.archived_threads(limit=500):
            async for message in thread.history(limit=1, oldest_first=True):
                if message is not None and message.created_at >= after:
                    game = Game(message)
                    if author in game:
                        games.append(game)

        if len(games) == 0:
            await interaction.followup.send(
                content="I couldn't find any games you participated in the last thirty days",
                ephemeral=True)
            return

        message = """
Here's the games I found which you've participated in the last 30 days:
"""

        for game in games:
            message += F"{game}\n"

        await interaction.followup.send(content=message, ephemeral=True)

    def get_configured_games(self, guild_id, *args):
        guild = utils.get_guild_from_config(self.config, guild_id)

        result = []
        for arg in args:
            result.append(utils.split_config_list(self.config.get(guild, arg, fallback=None)))

        return tuple(result)


async def setup(bot: commands.Bot):
    config = configparser.ConfigParser()
    config.read('config/games.ini')
    cog = MyGames(bot=bot, config=config)
    await bot.add_cog(cog)


class Host:
    def __init__(self, hosts_line: str | None):
        """
        Parses the host line from a Message Embed into this object

        :param hosts_line: The raw Host line taken from the game's Embed
        """
        if hosts_line is None:
            self._host_id = None
            return

        match = HOST_MENTION_REGEX.match(hosts_line)

        if match is None:
            self._host_id = None
        else:
            self._host_id = int(match.group(1))

    def __eq__(self, value, /) -> bool:
        if isinstance(value, str):
            return self._host_id == int(value)
        if isinstance(value, int):
            return self._host_id == value
        if isinstance(value, discord.User) or isinstance(value, discord.Member):
            return self._host_id == value.id
        if isinstance(value, Host):
            return self._host_id == value._host_id
        return super().__eq__(value)

    def __str__(self) -> str:
        if self._host_id is None:
            return ""
        else:
            return F"<@{self._host_id}>"


class Guests:
    def __init__(self, guests_line: str | None):
        """
        Parses the guests line from a Message Embed into this object

        :param guests_line: The raw guests line taken from the game's Embed
        """
        if guests_line is None:
            self._guest_ids = []
        else:
            self._guest_ids = list(match.group(1) for match in GUESTS_MENTION_REGEX.finditer(guests_line))

    def __contains__(self, user):
        """
        Checks whether a particular Discord user, with the given ID, is present in the guests list
        :param item: The Discord user ID to check
        """
        if isinstance(user, str):
            return int(user) in self._guest_ids
        if isinstance(user, int):
            return user in self._guest_ids
        if isinstance(user, discord.User) or isinstance(user, discord.Member):
            return user.id in self._guest_ids
        return False

    def __str__(self):
        """
        Outputs the list of guests, as Discord mentions, as a comma-separated string.
        """
        return ", ".join([F"<@{guest_id}>" for guest_id in self._guest_ids])


class Game:
    def __init__(self, message: discord.Message):
        """
        Parses a Discord message into a structured Game object by parsing the Embeds that would have been created
        by the matchmaking command.

        :param message: The Discord message to parse
        """
        self._title = None
        self._host = None
        self._guests = None
        self._url = None

        thread: Optional[Thread] = message.thread

        if thread is None:
            return

        self._url = thread.jump_url

        if len(message.embeds) > 0:
            embed = message.embeds[0]
            self._title = embed.description
            for field in embed.fields:
                if field.name == "Host":
                    self._host = Host(field.value)
                elif field.name is not None and field.name.startswith("Guests"):
                    self._guests = Guests(field.value)

    def __contains__(self, user) -> bool:
        """
        Returns whether the specified Discord User ID is one of the players in this game (either the host or the
        guests).

        :param user: A Discord User to check
        :return: Whether the given User is a player in this game
        """
        if self._host is not None and self._host == user:
            return True

        return self._guests is not None and user in self._guests

    def __str__(self) -> str:
        """
        Outputs this Game in a nice table format to be displayed in a Discord message

        :return: A string representation of this Game
        """
        return F"* [{self._title}]({self._url}) | Host: {self._host or '-'} | Guests: {self._guests or '-'}"
