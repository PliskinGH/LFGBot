"""Data models for the matchmaking cog: GameOption, GuildGamesConfig, LFGContext."""

import re

import discord

from common import utils


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
                 users_to_notify: set[discord.Member] | None = None,
                 game_settings: dict[str, list[str]] | None = None):
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
        self.game_settings = game_settings
        if (self.game_settings is None):
            self.game_settings = {}

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

        # Recover Game Settings from the Settings field
        game_settings = {}
        for field in embed.fields:
            if field.name != "Settings":
                continue
            for line in field.value.splitlines():
                if (": " in line):
                    key, rest = line.split(": ", 1)
                    values = [v.strip() for v in rest.split(",") if v.strip()]
                    if (values):
                        game_settings[key] = values
            break

        context = cls(game_option=game_option,
                      host=host, target_role=target_role,
                      max_guests=max_guests, guests=guests,
                      users_to_notify=users_to_notify,
                      game_settings=game_settings)

        return context
