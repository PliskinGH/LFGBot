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

from cogs.matchmaking import LFGContext, Matchmaking
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
    """A channel stub capturing thread-creation requests."""

    def __init__(self, id=1, name="general"):
        self.id = id
        self.name = name
        self.type = discord.ChannelType.text
        self.mention = f"<#{self.id}>"
        self.owner_id = None
        self.created_kwargs = None

    async def create_thread(self, **kwargs):
        self.created_kwargs = kwargs
        # discord.py returns a 2-tuple when creating in a forum channel.
        return (None, None)

    async def fetch_message(self, message_id):
        return None

    async def edit(self, **kwargs):
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

    async def send_message(self, content=None, embed=None, ephemeral=False,
                           view=None, **kwargs):
        self.messages.append((content, embed, ephemeral, view))
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
    def __init__(self, commands=None):
        self._commands = commands or []

    def get_commands(self):
        return self._commands

    def get_command(self, name):
        for command in self._commands:
            if command.name == name:
                return command
        return None

    async def sync(self, *args, **kwargs):
        return list(self._commands)


class FakeBot:
    """A stand-in for ``commands.Bot`` for methods under test."""

    def __init__(self, commands=None):
        self.tree = FakeTree(commands or [])
        self.user = FakeMember(1, "LFGBot")
        self._cogs = {}
        self._channels = {}

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
def rolls_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(FIXTURES / "rolls.ini")
    return config


@pytest.fixture
def fake_bot():
    return FakeBot()


@pytest.fixture
def matchmaking(fake_bot, games_config) -> Matchmaking:
    return Matchmaking(bot=fake_bot, config=games_config)


@pytest.fixture
def descriptions():
    return [
        {"title": "Autumn", "category": "Map", "color": 14520159},
        {"title": "Winter", "category": "Map", "color": 16514303},
        {"title": "Lake", "category": "Map", "color": 1752220},
        {"title": "The Tower", "category": "Landmark", "color": 5127742},
        {"title": "The Ferry", "category": "Landmark", "color": 11427369},
    ]


@pytest.fixture
def matchrolls(fake_bot, rolls_config, descriptions) -> MatchRolls:
    return MatchRolls(bot=fake_bot, config=rolls_config, descriptions=descriptions)