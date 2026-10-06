"""The panel's reads of the bot's servers, through a REST-only Discord client.

One ``DiscordReads`` per web process, owned by the app (``app.state.discord``):
it logs the bot token in for REST calls and never connects to the gateway, so
the bot keeps its session. The typed objects save hand-parsing JSON, and the
rate-limit handling covers a page that asks Discord several things. The panel's
own login lives in ``auth``: the OAuth code grant is not part of discord.py.
"""

import time
from typing import Any

import discord

from common.constants import MENTION_RE

from . import settings

# Read once and kept briefly: a page render must not wait on Discord twice.
GUILDS_TIMEOUT = 60
LIST_TIMEOUT = 300

# Channel types the panel offers: where an LFG post goes, and the forums.
TEXT_CHANNEL_TYPES = (discord.ChannelType.text, discord.ChannelType.news)
FORUM_CHANNEL_TYPES = (discord.ChannelType.forum,)
ALL_CHANNEL_TYPES = TEXT_CHANNEL_TYPES + FORUM_CHANNEL_TYPES

# What one of these reads raises when it fails or cannot be trusted.
UNREACHABLE = (discord.HTTPException, KeyError, ValueError, TimeoutError)


class DiscordReads:
    """The Discord reads of one panel process, and the client they go through."""

    def __init__(self, token: str | None = None):
        self.token = token if token is not None else settings.DISCORD_TOKEN
        # Set by open(); a test sets it to a stand-in instead.
        self.client: discord.Client | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    async def open(self) -> None:
        """Log the REST client in with the bot token, never touching the gateway.

        A token Discord refuses leaves the panel usable: the pages then show
        the mentions the configuration stores instead of the names behind them.
        """
        if (not self.token):
            self._unreadable("DISCORD_TOKEN is not set")
            return
        client = discord.Client(intents=discord.Intents.none())
        try:
            await client.login(self.token)
        except (discord.LoginFailure, discord.HTTPException, OSError) as error:
            self._unreadable(str(error))
            await client.close()
            return
        self.client = client

    async def close(self) -> None:
        """Close the client, releasing the session it reads through."""
        if (self.client is not None):
            await self.client.close()
            self.client = None

    def can_read(self) -> bool:
        """Whether Discord can be read at all, i.e. the client is logged in."""
        return self.client is not None

    async def guilds(self) -> list[dict]:
        """Every server the bot is in, as ``{id, name}`` (cached briefly)."""
        cached = self._remembered("guilds")
        if (cached is not None):
            return cached
        if (self.client is None):
            return []
        guilds = [{"id": guild.id, "name": guild.name}
                  async for guild in self.client.fetch_guilds(limit=None)]
        return self._remember("guilds", guilds, GUILDS_TIMEOUT)

    async def name_of(self, guild_id: int) -> str | None:
        """The name Discord knows for a server, or None when it cannot be read."""
        try:
            guilds = await self.guilds()
        except UNREACHABLE:
            return None
        return next((guild["name"] for guild in guilds
                     if guild["id"] == guild_id), None)

    async def channels(
            self, guild_id: int,
            types: tuple[discord.ChannelType, ...] = TEXT_CHANNEL_TYPES,
            ) -> list[dict]:
        """The guild's channels of the given types, as picker choices."""
        return [{"id": channel.id, "label": f"#{channel.name}"}
                for channel in await self._channels(guild_id)
                if channel.type in types]

    async def roles(self, guild_id: int) -> list[dict]:
        """The guild's mentionable roles, as picker choices.

        The everyone role and the roles Discord manages are left out: a game
        can mention neither.
        """
        return [{"id": role.id, "label": f"@{role.name}"}
                for role in await self._roles(guild_id)
                if not role.is_default() and not role.managed]

    async def forum_tags(self, channel_id: int) -> list[dict]:
        """A forum channel's available tags, as picker choices."""
        cached = self._remembered(f"tags:{channel_id}")
        if (cached is not None):
            return cached
        if (self.client is None):
            return []
        channel = await self.client.fetch_channel(channel_id)
        tags = [{"id": tag.id, "label": tag.name}
                for tag in getattr(channel, "available_tags", [])]
        return self._remember(f"tags:{channel_id}", tags, LIST_TIMEOUT)

    async def labels(self, guild_id: int) -> dict[str, str]:
        """The guild's channels and roles, keyed by the mention form stored."""
        labels = {channel_mention(choice["id"]): choice["label"]
                  for choice in await self.channels(guild_id, ALL_CHANNEL_TYPES)}
        labels.update({role_mention(choice["id"]): choice["label"]
                       for choice in await self.roles(guild_id)})
        return labels

    async def _guild(self, guild_id: int):
        """The guild object, read once and kept briefly (None without a client)."""
        cached = self._remembered(f"guild:{guild_id}")
        if (cached is None):
            if (self.client is None):
                return None
            cached = self._remember(f"guild:{guild_id}",
                                    await self.client.fetch_guild(guild_id),
                                    LIST_TIMEOUT)
        return cached

    async def _channels(self, guild_id: int) -> list:
        """The guild's channels, read once and kept briefly."""
        cached = self._remembered(f"channels:{guild_id}")
        if (cached is not None):
            return cached
        guild = await self._guild(guild_id)
        if (guild is None):
            return []
        return self._remember(f"channels:{guild_id}",
                              list(await guild.fetch_channels()), LIST_TIMEOUT)

    async def _roles(self, guild_id: int) -> list:
        """The guild's roles, read once and kept briefly."""
        cached = self._remembered(f"roles:{guild_id}")
        if (cached is not None):
            return cached
        guild = await self._guild(guild_id)
        if (guild is None):
            return []
        return self._remember(f"roles:{guild_id}",
                              list(await guild.fetch_roles()), LIST_TIMEOUT)

    def _remember(self, key: str, value: Any, timeout: int) -> Any:
        """Cache a value for a while and return it."""
        self._cache[key] = (time.monotonic() + timeout, value)
        return value

    def _remembered(self, key: str) -> Any:
        """The cached value of a key, or None when it is missing or stale."""
        entry = self._cache.get(key)
        if (entry is None or entry[0] <= time.monotonic()):
            return None
        return entry[1]

    def _unreadable(self, reason: str) -> None:
        """Say why names cannot be read from Discord, and carry on."""
        print(f"Web panel: Discord cannot be read ({reason}): channels and roles "
              "will show as the mentions the configuration stores.")


def channel_mention(channel_id: int) -> str:
    """A channel id in the mention form the configuration stores."""
    return f"<#{channel_id}>"


def role_mention(role_id: int) -> str:
    """A role id in the mention form the configuration stores."""
    return f"<@&{role_id}>"


def mention_id(value: str | None) -> int | None:
    """The id in a channel or role mention, or None when it is not one."""
    if (not value):
        return None
    match = MENTION_RE.fullmatch(value.strip())
    return int(match.group(1)) if (match) else None