"""Shared fixtures and lightweight Discord fakes for the LFG bot test suite.

The bot's cog methods take ``discord.Interaction`` objects and drive the real
Discord HTTP API through them. For tests we substitute small ``Fake*`` classes
that record the calls made against ``interaction.response``, ``interaction.
followup``, ``channels``, ``members``, etc. without touching the network.
"""
from __future__ import annotations

import configparser
import sys
from pathlib import Path

import discord
import pytest

# Make the project root importable: tests run from the repo root via pytest.ini,
# but keep this explicit so the suite also works if invoked from elsewhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.models import LFGContext
from cogs.matchrolls import MatchRolls


class FakeMentionable:
    """Mimics the small surface of ``discord.Member``/``discord.Role`` we use."""

    def __init__(self, id: int, name: str):
        self.id = id
        self.name = name
        self.display_name = name
        self.colour = discord.Colour(0x2E3136)
        self.display_avatar = None

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


class FakeMember(FakeMentionable):
    async def send(self, content=None, **kwargs):
        self.sent_content = content
        return None


class FakeGuild:
    def __init__(self, id=1, members=None, roles=None, channels=None):
        self.id = id
        self.members = members or {}
        self.roles = roles or {}
        self.channels = channels or {}

    def get_member(self, member_id):
        return self.members.get(member_id)

    async def fetch_member(self, member_id):
        return self.members.get(member_id)

    def get_role(self, role_id):
        return self.roles.get(role_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


class FakeChannel:
    """A channel stub capturing thread-creation/rename requests."""

    def __init__(self, id=1, name="general", type_=None):
        self.id = id
        self.name = name
        self.type = type_ or discord.ChannelType.text
        self.mention = f"<#{self.id}>"
        self.owner_id = None
        self.created_kwargs = None
        self.message = None  # FakeMessage returned by fetch_message
        self.edited_kwargs = None

    async def create_thread(self, **kwargs):
        self.created_kwargs = kwargs
        # discord.py returns a 2-tuple when creating in a forum channel.
        return (None, None)

    async def fetch_message(self, message_id):
        return self.message

    async def edit(self, **kwargs):
        self.edited_kwargs = kwargs
        return None


class FakeMessage:
    def __init__(self, embeds=None):
        self.embeds = embeds or []
        self.edited = None
        self.jump_url = "https://discord.com/channels/1/1/1"

    async def edit(self, **kwargs):
        self.edited = kwargs
        return None


class FakeResponse:
    """Stands in for ``discord.Interaction.response``."""

    def __init__(self):
        self.done = False
        self.messages = []
        self.modals = []
        self.edited = None
        self.deferred = None

    def is_done(self):
        return self.done

    async def defer(self, ephemeral=False, **kwargs):
        self.deferred = ephemeral
        return None

    async def send_message(self, content=None, embed=None, embeds=None,
                           ephemeral=False, view=None, **kwargs):
        if (embed is not None):
            embeds = [embed] + (embeds or [])
        self.messages.append((content, embeds, ephemeral, view))
        self.done = True
        return None

    async def edit_message(self, **kwargs):
        self.edited = kwargs
        return None

    async def send_modal(self, modal, **kwargs):
        self.modals.append(modal)
        return None


class FakeFollowUp:
    """Stands in for ``discord.Interaction.FollowUp``."""

    def __init__(self):
        self.sent = []
        self.deleted = False

    async def send(self, content=None, embed=None, ephemeral=False,
                   view=None, **kwargs):
        self.sent.append((content, ephemeral, embed, view))
        return None

    async def delete_original_response(self, **kwargs):
        self.deleted = True
        return None


class FakeInteraction:
    def __init__(self, user, guild=None, message=None, channel=None,
                 guild_id=None):
        self.user = user
        self.guild = guild or FakeGuild()
        self.message = message
        self.channel = channel or FakeChannel()
        self.guild_id = guild_id if guild_id is not None else self.guild.id
        self.response = FakeResponse()
        self.followup = FakeFollowUp()


class FakeCommand:
    def __init__(self, name):
        self.name = name


class FakeTree:
    """Stands in for ``discord.app_commands.CommandTree``.

    Supports global commands (``self._commands``) and per-guild commands
    (``self._guild_commands``) keyed by guild id, mirroring the two code paths
    the bot uses.
    """

    def __init__(self, commands=None):
        self._commands = commands or []
        self._guild_commands = {}
        # Record of sync targets: guild ids, or None for a global sync.
        self.sync_calls = []

    def add_command(self, command, /, *, guild=None, guilds=None, override=False):
        if guild is not None:
            self._guild_commands.setdefault(guild.id, []).append(command)
        elif guilds is not None:
            for g in guilds:
                self._guild_commands.setdefault(g.id, []).append(command)
        else:
            self._commands.append(command)

    def get_commands(self, *, guild=None, type=None):
        if guild is None:
            return list(self._commands)
        return list(self._guild_commands.get(guild.id, []))

    def get_command(self, name, *, guild=None, type=None):
        for command in self.get_commands(guild=guild):
            if command.name == name:
                return command
        return None

    async def sync(self, *args, guild=None, **kwargs):
        self.sync_calls.append(None if guild is None else guild.id)
        return list(self.get_commands(guild=guild))


class FakeBot:
    """A stand-in for ``commands.Bot`` for methods under test."""

    def __init__(self, commands=None):
        self.tree = FakeTree(commands or [])
        self.user = FakeMember(1, "LFGBot")
        self._cogs = {}
        self._channels = {}
        self.provided_guild_ids: set = set()

    def get_cog(self, cog_name):
        return self._cogs.get(cog_name)

    def add_cog(self, cog):
        self._cogs[cog.__class__.__name__] = cog
        return cog

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)


# --------------------------------------------------------------------------- #
# Config fixtures
# --------------------------------------------------------------------------- #

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def games_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(FIXTURES / "games.ini")
    return config


@pytest.fixture
def game_parameters_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(FIXTURES / "games_parameters.ini")
    return config


@pytest.fixture
def rolls_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(FIXTURES / "rolls.ini")
    return config


@pytest.fixture
def fake_bot():
    return FakeBot()


@pytest.fixture
def matchmaking(fake_bot, games_config, game_parameters_config) -> Matchmaking:
    return Matchmaking(bot=fake_bot, config=games_config,
                       game_parameters=game_parameters_config)


@pytest.fixture
def descriptions():
    return [
        {"title": "Alpha", "category": "Map", "color": 14520159},
        {"title": "Beta", "category": "Map", "color": 16514303},
        {"title": "Gamma", "category": "Map", "color": 1752220},
        {"title": "Delta", "category": "Landmark", "color": 5127742},
        {"title": "Epsilon", "category": "Landmark", "color": 11427369},
    ]


@pytest.fixture
def matchrolls(fake_bot, rolls_config, descriptions) -> MatchRolls:
    return MatchRolls(bot=fake_bot, config=rolls_config, descriptions=descriptions)