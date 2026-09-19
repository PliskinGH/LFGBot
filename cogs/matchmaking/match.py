"""Match handling: thread creation, registration check, and match API registration."""

import aiohttp
import discord

from . import constants
from . import utils
from .models import LFGContext

# DRF metadata marks fields accepting several values with one of these types.
# DRF's SimpleMetadata labels MultipleChoiceField as "multiple choice"; the
# underscore variant is accepted too for API servers that normalize it.
MULTI_VALUE_FIELD_TYPES = ("multiple choice", "multiple_choice", "list")


class MatchMixin:
    """Match lifecycle after the LFG succeeds: thread creation and match API registration."""

    async def start_game_matches(self, interaction: discord.Interaction, context: LFGContext,
                                 message: discord.Message = None):
        """Start the games of an LFG post: one thread per game, then one
        match registration per thread."""
        if (message is None):
            message = interaction.message
        if (message is None):
            return
        game_option = context.game_option
        host = context.host
        guests = context.guests
        embed = message.embeds[0] if message.embeds else None

        # Create thread(s)
        # 3 cases: a) Do nothing if this message already has a thread
        #          b) Create thread(s) in a (forum) channel if available
        #          c) Create thread(s) under this message otherwise
        # One thread is created per game (see _create_game_threads), then
        # each thread gets its own match registration.
        if (getattr(message, "thread", None) is not None):
            return

        # The forum is stored as a mention, resolved like roles; a game
        # without one (or with an unresolvable one) posts in the LFG channel.
        forum = None
        forum_id = None
        if (game_option.forum):
            forum_id = utils.get_id_from_mention(game_option.forum)
        if (forum_id):
            forum = self.bot.get_channel(forum_id)

        # Thread title = the LFG title, else the description (legacy posts).
        thread_title = utils.thread_title_from_embed(embed)

        # One thread per game: the same players can then play several games
        # (each with its own match registration) from a single post.
        created_threads = await self._create_game_threads(
            interaction, context, message, embed, forum, thread_title)

        if (created_threads):
            if (forum is not None):
                embed.url = created_threads[0].jump_url
            else:
                embed.url = message.jump_url
            try:
                await message.edit(embed=embed)
            except Exception as error:
                print(error)

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
            if (game_option.api_token):
                auth_token = game_option.api_token
            website_url = None
            if (game_option.website_url):
                website_url = game_option.website_url
            registration_url = None
            if (game_option.registration_url):
                registration_url = game_option.registration_url
            profile_url = None
            if (game_option.profile_url):
                profile_url = game_option.profile_url

            verified_users = []
            if registration_api_url:
                try:
                    # Registration is a property of the players, who are the
                    # same in every game: check it once, for all the threads.
                    users = [host] + list(guests)
                    verified_users = await self.check_registration(
                        created_threads[0], users, registration_api_url,
                        gameName, auth_token,
                        website_url, registration_url, profile_url)
                except Exception as error:
                    print(f"League player registration check failed: {error}")
            if match_api_url:
                for thread in created_threads:
                    try:
                        await self.register_match(
                            thread, match_api_url, match_url,
                            auth_token,
                            getattr(thread, "name", None) or thread_title,
                            gameName, verified_users,
                            game_settings=context.game_settings,
                            game_command=(context.game_option.command
                                          if context.game_option else None),
                            guild_id=interaction.guild_id,
                            website_url=website_url)
                    except Exception as error:
                        print(f"League match registration request failed: {error}")

    async def _create_game_threads(self, interaction, context,
                                   message, embed, forum, thread_title) -> list:
        """Create one thread per game for a started LFG post:
        the extras threads beyond the first cannot attach to the LFG message and are standlone;
        failures are reported and skipped.
        """
        channel = interaction.channel
        host = context.host
        guests = context.guests
        game_option = context.game_option
        nb_games = context.nb_games

        thread_in_forum = forum is not None
        thread_channel = forum if thread_in_forum else channel
        parent_message = message
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

        thread_visibility = True
        thread_tag = None

        keywords = {}
        if (thread_in_forum):
            tag_name = ""
            if (game_option.tag):
                tag_name = game_option.tag
            if (len(tag_name)):
                for forum_tag in forum.available_tags:
                    if (forum_tag.name == tag_name):
                        thread_tag = forum_tag
        if (game_option.visibility):
            visibility = game_option.visibility
            if (len(visibility) and int(visibility) == 0):
                thread_visibility = False

        if (thread_in_forum):
            if (thread_tag is not None):
                keywords['applied_tags'] = [thread_tag]
            keywords['content'] = thread_message
        if (not(thread_visibility)):
            keywords['type'] = discord.ChannelType.private_thread

        created_threads = []
        for game_index in range(nb_games):
            thread_keywords = dict(keywords)
            if (nb_games > 1):
                # The (i/n) prefix comes first so it survives the
                # 100-character cap, which keeps the beginning of the name.
                thread_keywords['name'] = utils.clean_thread_title(
                    f"({game_index + 1}/{nb_games}) {thread_title}")
            else:
                thread_keywords['name'] = thread_title
            # Per-thread parentage: a message holds a single thread, so only
            # the first game's thread can attach to the LFG message; the
            # other games are standalone threads that carry their own copy of
            # the post's embed instead.
            thread_has_parent = (game_index == 0 and not(thread_in_forum)
                                 and thread_visibility)
            thread_embed = None
            if (thread_has_parent):
                thread_keywords['message'] = parent_message
            else:
                thread_embed = embed.copy()
                thread_embed.url = message.jump_url
                thread_embed.remove_footer()
                if (thread_in_forum):
                    thread_keywords['embed'] = thread_embed
                elif (game_index and thread_visibility):
                    # The extra games cannot attach to the LFG message: they
                    # become standalone public threads in the same channel.
                    thread_keywords['type'] = discord.ChannelType.public_thread
            try:
                thread = await thread_channel.create_thread(**thread_keywords)
                if (thread_in_forum):
                    thread, _ = thread
            except Exception as error:
                print(error)
                continue
            if (thread is None):
                continue
            try:
                if (not(thread_in_forum)):
                    await thread.send(content=thread_message, embed=thread_embed)
            except Exception as error:
                print(error)
            created_threads.append(thread)
        return created_threads

    async def _get_multi_value_fields(self, match_api_url: str,
                                      auth_token: str | None = None
                                      ) -> set[str] | None:
        """Return the set of match API fields accepting several values.

        Discovered via an OPTIONS request on the match endpoint (DRF
        metadata: ``actions.POST.<field>.type`` is ``multiple_choice`` or
        ``list`` for multi-value fields), so the bot stays agnostic of the
        API's field types. Cached per URL; returns None when the metadata
        cannot be read (callers then treat every field as single-valued).
        """
        if (match_api_url in self._match_api_metadata):
            return self._match_api_metadata[match_api_url]
        headers = {}
        if (auth_token):
            headers["Authorization"] = f"Token {auth_token}"
        fields = None
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.options(match_api_url) as response:
                    if (response.status == 200):
                        metadata = await response.json()
                        actions = metadata.get("actions", {}).get("POST", {})
                        fields = {
                            name for name, info in actions.items()
                            if info.get("type") in MULTI_VALUE_FIELD_TYPES
                        }
                        self._match_api_metadata[match_api_url] = fields
        except Exception as error:
            print(f"Failed to read match API metadata ({match_api_url}): {error}")
        return fields

    @staticmethod
    def _add_game_settings_payload(payload, game_settings, field_map,
                                   multi_value_fields):
        """Merge game parameters into a match registration payload.

        ``field_map`` maps parameter names to the match API field names
        (declared in games_parameters.ini) and ``multi_value_fields`` is the
        set of API fields accepting several values (from the API metadata).
        Single-value fields are wired only when exactly one value was
        submitted; with several values the parameter is left blank.
        Multi-value fields are wired as a list; the API enforces their limits
        and rejects the submission when exceeded. When the metadata is
        unknown (None), every field is treated as single-valued.
        """
        if (not game_settings):
            return
        for param_name, values in game_settings.items():
            api_field = field_map.get(param_name)
            if (not api_field):
                continue
            if (multi_value_fields is not None and api_field in multi_value_fields):
                payload[api_field] = list(values)
            elif (len(values) == 1):
                payload[api_field] = values[0]

    async def register_match(self, thread, match_api_url, match_url,
                             auth_token, title, website_name, verified_users,
                             game_settings=None, game_command=None,
                             guild_id=None, website_url=None):
        headers = {"Content-Type": "application/json"}
        if (auth_token):
            headers["Authorization"] = f"Token {auth_token}"

        # The match API field names for the fixed payload components come from
        # games_parameters.ini: the [DEFAULT] section's api_* keys apply to
        # every game (default_api_fields), and each game's own section may
        # override them (per guild). A component is only sent when its field is declared.
        api_fields = dict(self.default_api_fields)
        api_fields.update(self.get_game_api_fields(guild_id, game_command))
        payload = {}

        title_field = api_fields.get(constants.API_TITLE_FIELD_KEY)
        if (title_field):
            payload[title_field] = title
        table_talk_field = api_fields.get(constants.API_TABLE_TALK_URL_FIELD_KEY)
        if (table_talk_field):
            payload[table_talk_field] = thread.jump_url

        if (game_settings):
            multi_value_fields = await self._get_multi_value_fields(
                match_api_url, auth_token)
            self._add_game_settings_payload(
                payload, game_settings, api_fields, multi_value_fields)

        participants_field = api_fields.get(constants.API_PARTICIPANTS_FIELD_KEY)
        discord_username_field = api_fields.get(constants.API_DISCORD_USERNAME_FIELD_KEY)
        if (verified_users and participants_field and discord_username_field):
            payload[participants_field] = [
                {discord_username_field: user.name}
                for user in verified_users
            ]

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(match_api_url, json=payload) as response:
                    if response.status not in (200, 201):
                        response_text = await response.text()
                        print(f"League match registration failed ({response.status}): {response_text}")
                        await self._send_registration_failure(
                            thread, website_name, website_url)
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
        except Exception as error:
            # The API could not be reached (connection refused, DNS failure,
            # timeout, ...) or its response could not be parsed: the match was
            # not registered. Post a failure message instead of a match URL,
            # so the players can submit a new game entry manually.
            print(f"League match registration request failed: {error}")
            await self._send_registration_failure(
                thread, website_name, website_url)
        return None

    @staticmethod
    async def _send_registration_failure(thread, website_name=None,
                                         website_url=None):
        """Post the failure message when a match could not be registered.

        The game was not registered, so the players must submit a new game
        entry manually; the website (name + link) is included when known.
        """
        message = (
            "Match registration failed. The game was not registered; "
            "please submit a new game entry manually"
        )
        if (website_name):
            if (website_url):
                message += f" on [{website_name}]({website_url})"
            else:
                message += f" on {website_name}"
        message += "."
        await thread.send(message)

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
