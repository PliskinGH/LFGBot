"""Shared fixtures and lightweight Discord fakes for the LFG bot test suite.

The bot's cog methods take ``discord.Interaction`` objects and drive the real
Discord HTTP API through them. For tests we substitute small ``Fake*`` classes
that record the calls made against ``interaction.response``, ``interaction.
followup``, ``channels``, ``members``, etc. without touching the network.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import sys
from base64 import b64decode
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import discord
import pytest
from itsdangerous import TimestampSigner
from starlette.testclient import TestClient

# Make the project root importable: tests run from the repo root via pytest.ini,
# but keep this explicit so the suite also works if invoked from elsewhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.matchmaking import constants as lfg_constants
from cogs.matchmaking.cog import Matchmaking
from cogs.matchmaking.models import LFGContext
from cogs.matchrolls import MatchRolls
from tortoise import connections
from tortoise.migrations.api.migrate import migrate as apply_migrations

from db.db import Database
from db.orm_config import orm_config


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
        self.parent = None  # parent channel when this channel is a thread
        self.created_kwargs = None
        self.created_thread_kwargs = []  # every create_thread request
        self.thread_factory = None  # optional: callable(**kwargs) -> thread
        self.message = None  # FakeMessage returned by fetch_message
        self.starter_message = None  # cached thread starter (discord.Thread)
        self.messages = []  # FakeMessage list iterated by history()
        self.edited_kwargs = None
        self.sent = []  # (content, embed, view) tuples recorded by send

    async def send(self, content=None, embed=None, view=None, **kwargs):
        self.sent.append((content, embed, view))
        return FakeMessage()

    async def create_thread(self, **kwargs):
        self.created_kwargs = kwargs
        self.created_thread_kwargs.append(kwargs)
        if (self.thread_factory is not None):
            # Test-provided factory building the created thread (a forum
            # channel returns a (thread, message) 2-tuple instead).
            return self.thread_factory(**kwargs)
        # discord.py returns a 2-tuple when creating in a forum channel.
        return (None, None)

    async def fetch_message(self, message_id):
        return self.message

    async def history(self, *, limit=100, oldest_first=None, **kwargs):
        messages = list(self.messages)
        if (not oldest_first):
            messages.reverse()
        for message in messages[:limit]:
            yield message

    async def edit(self, **kwargs):
        self.edited_kwargs = kwargs
        return None


class FakeButton:
    """A fake Discord component (button/select) with just a custom id."""

    def __init__(self, custom_id):
        self.custom_id = custom_id


class FakeActionRow:
    """A fake action row holding fake components."""

    def __init__(self, *custom_ids):
        self.children = [FakeButton(custom_id) for custom_id in custom_ids]


def lfg_view_components():
    """Component rows mimicking a posted LFG message's still-active view."""
    return [FakeActionRow(*lfg_constants.LFG_VIEW_CUSTOM_IDS)]


class FakeMessage:
    def __init__(self, embeds=None, id=1, components=None):
        self.id = id
        self.embeds = embeds or []
        # ``None`` means "unknown": utils.has_lfg_view then assumes the LFG
        # view is still there. Pass a list to simulate a specific state.
        self.components = components
        self.edited = None
        self.jump_url = "https://discord.com/channels/1/1/1"

    async def edit(self, **kwargs):
        self.edited = kwargs
        # Mirror Discord: editing with view=None removes the message's
        # components, so a later check sees the game as closed.
        if ("view" in kwargs and kwargs["view"] is None):
            self.components = []
        return None


class FakeThread:
    """A created thread stub capturing the messages posted into it."""

    def __init__(self, name=None, id=5000):
        self.id = id
        self.name = name
        self.mention = f"<#{self.id}>"
        self.jump_url = f"https://discord.com/channels/1/1/{self.id}"
        self.owner_id = None
        self.parent = None
        self.starter_message = None
        self.sent = []  # contents recorded by send
        self.sent_embeds = []  # embeds recorded by send, parallel to sent

    async def send(self, content=None, embed=None, **kwargs):
        self.sent.append(content)
        self.sent_embeds.append(embed)
        return FakeMessage()


# Sentinel mirroring discord.utils.MISSING: lets the fake tell an omitted
# argument apart from an explicitly passed None, like the real library does.
_MISSING = object()


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

    async def send_message(self, content=None, embed=None,
                           embeds=_MISSING, ephemeral=False, view=None,
                           **kwargs):
        # Mirror discord.http.handle_message_parameters: embeds participates
        # with a MISSING default there, and an explicitly passed embeds=None
        # crashes in it (len(None)). Callers must omit the argument instead.
        if (embeds is not _MISSING):
            if (embeds is None):
                raise TypeError("object of type 'NoneType' has no len()")
            if (len(embeds) > 10):
                raise ValueError("embeds has a maximum of 10 elements.")
        recorded_embeds = None if (embeds is _MISSING) else embeds
        if (embed is not None):
            recorded_embeds = [embed] + (recorded_embeds or [])
        self.messages.append((content, recorded_embeds, ephemeral, view))
        self.done = True
        return None

    async def edit_message(self, **kwargs):
        self.edited = kwargs
        return None

    async def send_modal(self, modal, **kwargs):
        self.modals.append(modal)
        return None


class FakeFollowUp:
    """Stands in for ``discord.Interaction.FollowUp``.

    Post-defer responses are sent here; they are *also* appended to the
    interaction's ``response.messages`` so that assertions written against
    the pre-defer ``response`` keep working.
    """

    def __init__(self, response=None):
        self.sent = []
        self.deleted = False
        self._response = response

    async def send(self, content=None, embed=None, ephemeral=False,
                   view=None, **kwargs):
        self.sent.append((content, ephemeral, embed, view))
        if (self._response is not None):
            embeds = kwargs.get("embeds")
            if (embeds is None):
                embeds = [embed] if (embed is not None) else None
            elif (embed is not None):
                embeds = [embed] + list(embeds)
            self._response.messages.append(
                (content, embeds, ephemeral, view))
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
        # The followup mirrors into the response so tests can assert on
        # either surface after a command has deferred.
        self.followup = FakeFollowUp(response=self.response)

    async def edit_original_response(self, **kwargs):
        # ``ConfirmView`` swaps the prompt for its outcome text this way.
        self.response.edited = kwargs
        return None


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

    def remove_command(self, name, *, guild=None):
        if guild is None:
            for index, command in enumerate(self._commands):
                if command.name == name:
                    return self._commands.pop(index)
            return None
        commands = self._guild_commands.get(guild.id)
        if (commands is None):
            return None
        for index, command in enumerate(commands):
            if command.name == name:
                return commands.pop(index)
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
        self.guilds: list = []
        self.provided_guild_ids: set = set()

    @property
    def cogs(self) -> dict:
        """The loaded cogs, as ``commands.Bot`` exposes them."""
        return self._cogs

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


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


def _test_database_url() -> str | None:
    """The URL of the test database, or None when unconfigured."""
    return os.getenv("TEST_DATABASE_URL")


def _safe_url(url: str) -> str:
    """A URL with the password masked, for error messages."""
    scheme, _, rest = url.partition("://")
    return f"{scheme}://***@{rest.partition('@')[2]}"


async def _drop_all_tables():
    """Drop the bot's tables and the migration history, for a clean test run."""
    await connections.get("default").execute_script(
        "DROP TABLE IF EXISTS config_changes, tortoise_migrations, "
        "roll_descriptions, "
        "roll_items, roll_categories, game_parameter_values, "
        "game_parameters, game_api_field_overrides, default_api_fields, "
        "games, guilds CASCADE")


@pytest.fixture
async def db():
    """A Database on the test database, from a clean migrated schema.

    The test database is provisioned by the environment (CI service or local
    Postgres). Like the deploy step, the schema is built from the committed
    migrations; each test drops it and re-applies them for isolation.
    ``Database.close()`` releases the global fallback context, so the next test
    can initialize again.
    """
    url = _test_database_url()
    if (not url):
        pytest.skip("No TEST_DATABASE_URL configured; skipping database tests.")
    database = Database(url)
    try:
        # Schema is built from the committed migrations (the deploy step);
        # each test drops it and re-applies them for isolation.
        await apply_migrations(config=orm_config(url))
        await _drop_all_tables()
        await apply_migrations(config=orm_config(url))
        await database.initialize()
    except Exception as error:
        await database.close()
        pytest.skip(f"Database at {_safe_url(url)} is unreachable: {error}")
    yield database
    await database.close()


# --------------------------------------------------------------------------- #
# Web panel
# --------------------------------------------------------------------------- #

# The session secret and cookie the web tests sign and read.
WEB_SESSION_SECRET = "test-session-secret"
WEB_SESSION_COOKIE = "lfgbot_admin"


@pytest.fixture
def web_app(monkeypatch):
    """The panel's app, with its credentials and session secret pinned.

    The app owns its Discord reads and no client is logged in, so a test
    reaches the database rather than the network; one that wants names or
    picker choices gives those reads a stand-in client (see ``reads``). The
    app is built without running its lifespan, so the database comes from the
    ``db`` fixture.
    """
    from admin_web import settings

    monkeypatch.setattr(settings, "SESSION_SECRET", WEB_SESSION_SECRET)
    monkeypatch.setattr(settings, "DISCORD_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "DISCORD_CLIENT_ID", "test-client")
    monkeypatch.setattr(settings, "DISCORD_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "OPERATOR_DISCORD_IDS", frozenset())

    from admin_web.app import create_app

    return create_app()


@pytest.fixture
def client(web_app) -> TestClient:
    """A test client on the panel."""
    return TestClient(web_app)


@pytest.fixture
def reads(web_app):
    """The app's Discord reads, whose client a test replaces with a stand-in."""
    return web_app.state.discord


@pytest.fixture
def login(monkeypatch):
    """Log a test client in through the panel's own OAuth callback.

    The identity Discord would report is stubbed, so the login is real
    everywhere else: the state check, the session cookie, the stored account.
    """
    def log(client: TestClient, *, user_id: int = 42, name: str = "Manager",
            guilds: dict[int, str] | None = None) -> None:
        from admin_web import auth

        async def fake_identity(code, redirect_uri):
            return {
                "account": {"id": str(user_id), "username": name.lower(),
                            "global_name": name},
                "guilds": [{"id": str(guild_id), "name": guild_name,
                            "permissions": str(auth.MANAGE_GUILD)}
                           for guild_id, guild_name in (guilds or {}).items()],
            }

        monkeypatch.setattr(auth, "fetch_identity", fake_identity)
        connect = client.get("/discord/connect", follow_redirects=False)
        state = parse_qs(urlsplit(connect.headers["location"]).query)["state"][0]
        done = client.get("/discord/callback",
                          params={"code": "code", "state": state},
                          follow_redirects=False)
        assert done.status_code == 303, done.text

    return log


def read_session(client: TestClient) -> dict:
    """The session the client's cookie carries, read back for assertions.

    A session the middleware cleared comes back as the JSON literal ``null``,
    which reads here as an empty session.
    """
    for cookie in client.cookies.jar:
        if (cookie.name == WEB_SESSION_COOKIE):
            data = cookie.value.encode("utf-8")
            return json.loads(b64decode(
                TimestampSigner(WEB_SESSION_SECRET).unsign(data))) or {}
    return {}


def csrf_of(client: TestClient, path: str = "/") -> str:
    """The CSRF token the page at ``path`` rendered into its forms."""
    page = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match is not None, f"no form was rendered at {path}"
    return match.group(1)


def be_operator(monkeypatch, user_id: int = 42) -> None:
    """Let the session's account operate on every server."""
    from admin_web import settings

    monkeypatch.setattr(settings, "OPERATOR_DISCORD_IDS", frozenset({user_id}))


# The Discord stand-ins the panel's reads go through, in place of a REST client.


class ApiTag:
    """A forum tag, as the REST client reads one."""

    def __init__(self, id: int, name: str):
        self.id = id
        self.name = name


class ApiChannel:
    """A channel of a given type, as the REST client reads one."""

    def __init__(self, id: int, name: str, type: discord.ChannelType,
                 tags: list | None = None):
        self.id = id
        self.name = name
        self.type = type
        self.available_tags = tags or []


class ApiRole:
    """A role, with the two flags the panel filters on."""

    def __init__(self, id: int, name: str, default: bool = False,
                 managed: bool = False):
        self.id = id
        self.name = name
        self.managed = managed
        self._default = default

    def is_default(self) -> bool:
        return self._default


class ApiMember:
    """A member, as the REST client reads one."""

    def __init__(self, id: int, name: str, nick: str | None = None,
                 global_name: str | None = None):
        self.id = id
        self.name = name
        self.nick = nick
        self.global_name = global_name


def _fake_member_name(member: ApiMember) -> str:
    """What the fake matches a search against and labels a member with."""
    return member.nick or member.global_name or member.name


class ApiGuild:
    """A guild, offering the two reads the panel makes of one."""

    def __init__(self, id: int, name: str, channels: list | None = None,
                 roles: list | None = None, members: list | None = None):
        self.id = id
        self.name = name
        self.channels = channels or []
        self.roles = roles or []
        self.members = {member.id: member for member in members or []}
        self.member_fetches = 0

    async def fetch_channels(self) -> list:
        return list(self.channels)

    async def fetch_roles(self) -> list:
        return list(self.roles)

    async def fetch_member(self, member_id: int) -> ApiMember:
        self.member_fetches += 1
        if (member_id not in self.members):
            # Discord answers NotFound for a member who is not there; a missing
            # key reads the same through the panel's UNREACHABLE.
            raise KeyError(member_id)
        return self.members[member_id]

    async def query_members(self, *, query: str | None = None,
                            limit: int = 5) -> list:
        wanted = (query or "").casefold()
        return [member for member in self.members.values()
                if wanted in _fake_member_name(member).casefold()][:limit]


class ApiHttp:
    """The one HTTP route the reads call directly, over a stand-in client."""

    def __init__(self, client: "ApiClient"):
        self.client = client
        self.paths: list[str] = []

    async def request(self, route, **kwargs) -> list[dict]:
        self.paths.append(route.path)
        params = kwargs.get("params") or {}
        members = await self.client.guild_by_id(route.guild_id).query_members(
            query=params.get("query"), limit=params.get("limit", 5))
        return [{"nick": member.nick,
                 "user": {"id": str(member.id), "username": member.name,
                          "global_name": member.global_name}}
                for member in members]


class ApiClient:
    """A stand-in for the REST client, counting the guild reads it served."""

    def __init__(self, guilds: list | None = None,
                 channels: list | None = None):
        self.guilds = list(guilds or [])
        self.known_channels = {channel.id: channel for channel in channels or []}
        self.guild_fetches = 0
        self.http = ApiHttp(self)

    def guild_by_id(self, guild_id: int) -> ApiGuild:
        """The guild the reads are about."""
        for guild in self.guilds:
            if (guild.id == guild_id):
                return guild
        raise AssertionError(f"no guild {guild_id} was set up")

    async def fetch_guilds(self, *, limit=None):
        for guild in self.guilds:
            yield guild

    async def fetch_guild(self, guild_id: int) -> ApiGuild:
        self.guild_fetches += 1
        return self.guild_by_id(guild_id)

    async def fetch_channel(self, channel_id: int) -> ApiChannel:
        return self.known_channels[channel_id]


def api_guild(guild_id: int = 7, name: str = "Server Seven",
              channels: list | None = None, roles: list | None = None,
              members: list | None = None) -> ApiGuild:
    """A guild with the channels, roles and members a test gives it."""
    return ApiGuild(guild_id, name, channels=channels, roles=roles,
                    members=members)


def api_client(*guilds: ApiGuild, forums: list | None = None) -> ApiClient:
    """A REST client stand-in over the given guilds, and the forums it can fetch."""
    return ApiClient(list(guilds), channels=forums)