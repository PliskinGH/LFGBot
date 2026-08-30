"""LFG interaction flow: channel guards, game resolution, modals, join/notify/cancel/start."""

from typing import Optional

import discord

from common import common
from common.utils import get_default_emoji_url, get_id_from_mention, indefinite_article

from . import constants
from . import utils
from .models import GameOption, LFGContext
from .views import GameSettingsModal, LFGView


class InteractionMixin:
    """LFG interaction flow: channel guards, game resolution, modals, join/notify/cancel/start."""

    async def _guard_lfg_channel(self, interaction, command_name):
        channel = interaction.channel
        if (channel is not None and channel.type in constants.THREAD_TYPES):
            await interaction.response.send_message(
                f"The `/{command_name}` command cannot be used inside a thread. "
                f"Use `/{constants.RENAME_COMMAND}` to rename a game thread.",
                ephemeral=True,
            )
            return False
        if (channel is None or channel.type != discord.ChannelType.text):
            await interaction.response.send_message(
                f"The `/{command_name}` command can only be used in a server channel.",
                ephemeral=True,
            )
            return False
        return True

    def _resolve_game_option(self, guild_id, game_identifier):
        guild_config = self.get_guild_config(guild_id)
        game_option = guild_config.games.get(game_identifier)
        if (game_option is None):
            # If discord unfortunately sent the name instead of value during
            # autocomplete we need to check the name too.
            for go in guild_config.games.values():
                if go.name == game_identifier:
                    game_option = go
                    break
        return game_option

    async def _reject_unknown_game(self, interaction, game_identifier):
        await interaction.response.send_message(
            f"`{game_identifier}` is not a configured game for this server.",
            ephemeral=True,
        )

    async def _direct_lfg(self, interaction, game_identifier, description,
                          max_players, game_settings=None):
        # Shared LFG-creation tail for the /lfg command (direct mode) and the
        # dynamically-generated per-game commands.
        game_option = self._resolve_game_option(interaction.guild_id, game_identifier)
        if (game_option is None):
            await self._reject_unknown_game(interaction, game_identifier)
            return

        if (max_players is not None and not (2 <= max_players <= 100)):
            await interaction.response.send_message(
                "`max_players` must be between 2 and 100.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        if (max_players is None):
            max_guests = game_option.default_max_guests
        else:
            max_guests = max_players - 1
        await self.create_lfg(
            interaction, game_option, description or "", max_guests,
            game_settings=game_settings,
        )

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
        game_command = select.values[0]
        await self._create_lfg_from_modal(interaction, modal, game_command)

    async def _create_lfg_from_modal(self,
                                     interaction: discord.Interaction,
                                     modal: discord.ui.Modal,
                                     game_command: str
    ):
        # Shared modal-confirmation tail for the guided mode of /lfg (after
        # the game selection view or when only the game argument is given)
        # and the per-game commands (game already known).
        await interaction.response.defer(ephemeral=True)

        game_option = self.get_guild_config(interaction.guild_id).games.get(game_command)

        max_players = modal.max_players_number
        if (max_players is None):
            max_guests = game_option.default_max_guests
        else:
            max_guests = max_players - 1
        await self.create_lfg(interaction, game_option, modal.description.value, max_guests)

    async def _send_game_settings_modal(self, interaction: discord.Interaction,
                                        game_identifier: str):
        # Shared modal route: the game is already known (per-game slash
        # commands, or /lfg with only the game argument), so the settings
        # modal opens directly, without the game selection view.
        # The modal deliberately holds only description and max_players:
        # game parameters (games_parameters.ini) are direct-arguments-only,
        # since Discord caps modals at 5 components.
        game_option = self._resolve_game_option(interaction.guild_id, game_identifier)
        if (game_option is None):
            await self._reject_unknown_game(interaction, game_identifier)
            return
        game_command = game_option.command

        async def on_confirm(modal_interaction: discord.Interaction,
                             modal: discord.ui.Modal,
                             _select: discord.ui.Select | None):
            await self._create_lfg_from_modal(
                modal_interaction, modal, game_command)

        await interaction.response.send_modal(
            GameSettingsModal(parent_select=None, on_confirm=on_confirm))

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
                if (previous_size + len(new_guest) + len(constants.GUESTS_OVER_LIMIT) <= 1024):
                    guests_string += new_guest
                elif (previous_size):
                    guests_string += constants.GUESTS_OVER_LIMIT
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
            if (context.game_settings):
                embed.add_field(
                    name="Settings",
                    value="\n".join(self._settings_lines(
                        context.game_option.command if context.game_option else None,
                        context.game_settings)),
                    inline=False,
                )
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
                              emoji=constants.EMOJI_CANCEL,
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
                              emoji=constants.EMOJI_START,
                              footer_text="Game already started. Sorry!")

        await self.create_game_thread(interaction, context)

        await interaction.followup.send(
            content=f"The game has started!", ephemeral=True
        )

    async def close_game(self, interaction: discord.Interaction,
                         emoji: str = constants.EMOJI_START,
                         footer_text: str = "Game closed/full. Sorry!"):
        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if (embed is not None):
            emoji_url = get_default_emoji_url(emoji)
            embed.set_footer(text=footer_text, icon_url=emoji_url)
            try:
                await message.edit(embed=embed)
            except Exception as error:
                print(error)

        await self.remove_view(interaction)

    def _settings_lines(self, game_command: str | None,
                        game_settings: dict[str, list[str]]) -> list[str]:
        """Render a game settings dict as display lines, mapping raw values to
        their display names from the game's parameter configuration. Unknown
        or already-displayed tokens are kept as-is."""
        param_mappings = self.game_parameters.get(game_command or "", {})
        return [
            f"{param_name}: {', '.join(utils.render_param_values(values, param_mappings.get(param_name, {})))}"
            for param_name, values in game_settings.items()
        ]

    async def create_lfg(self, interaction: discord.Interaction,
                         game_option: GameOption,
                         description: str,
                         max_guests: int | None,
                         game_settings: Optional[dict[str, list[str]]] = None):
        # Create an LFG post
        # Embed + buttons to interact

        embed = discord.Embed(description=description)
        embed.set_footer(text=constants.LFG_FOOTER_HELP)
        
        if (len(game_option.role)):
            embed.add_field(name="Target", value=game_option.role, inline=True)
        
        host = interaction.user
        field_text = host.mention
        embed.add_field(name="Host", value=field_text, inline=True)

        if (max_guests is not None):
            embed.add_field(name=f"Guests (0/{max_guests})", value="", inline=False)
        
        if (game_settings):
            embed.add_field(name="Settings", value="\n".join(self._settings_lines(game_option.command, game_settings)), inline=False)
        
        author_avatar = common.DEFAULT_AVATAR_URL
        display_avatar = host.display_avatar
        if (display_avatar is not None):
            author_avatar = display_avatar.url
        embed.set_author(name=host.display_name,
                         icon_url=author_avatar)
        
        embed.title = "Looking for " 
        embed.title += indefinite_article(game_option.name)
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
            role_id = get_id_from_mention(game_option.role)
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
                message_to_send += constants.EMOJI_START + ", which will ping"
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
