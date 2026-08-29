"""Match handling: thread creation, registration check, and match API registration."""

import os

import aiohttp
import discord

from common import utils

from .models import LFGContext


class MatchMixin:
    """Match lifecycle after the LFG succeeds: thread creation and match API registration."""

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
