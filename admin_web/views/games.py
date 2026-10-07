"""The game pages: add, edit, copy and remove a server's games and parameters.

Every write goes through the cog's own ``db_config`` writers and logs one
``ConfigChange`` the way the matching slash command does, so the bot's watcher
applies it and the log reads the same whichever surface made the change. The
API token is the exception: it never appears in a form, only in two posts of its
own (set and clear).
"""

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse
from starlette.routing import Route

from cogs.matchmaking import constants as mm_constants
from cogs.matchmaking import db_config as games_db_config
from cogs.matchmaking import validation

from .. import auth, discord_reads, forms, overview, pages
from . import sections

DEFAULT_GUILD_ID = mm_constants.DEFAULT_GUILD_ID


def routes() -> list[Route]:
    """The game pages of one server, and the same ones for [DEFAULT]."""
    return sections.routes(PAGES)


async def new_game(request, guild_id: int):
    """Add a game to a server (or to [DEFAULT])."""
    context = await overview.blank_game(request.app.state.discord, guild_id)
    if (request.method == "GET"):
        return _page(request, guild_id, "game_form.html", new=True, **context)
    form = await pages.submitted(request)
    arguments, errors = forms.game_form(form)
    if (not errors):
        overrides = {key: value
                     for key, value in arguments["api_fields"].items()
                     if value is not None}
        created = await games_db_config.add_game(
            guild_id, arguments["command"], api_fields=overrides or None,
            **arguments["fields"])
        if (not created):
            errors.append(f"`{arguments['command']}` is already configured here.")
    if (errors):
        context["values"] = pages.submitted_values(form)
        context["errors"] = errors
        return _page(request, guild_id, "game_form.html", new=True, **context)
    await pages.log_change(request, guild_id, "game.add", arguments["command"])
    auth.flash(request, "success", f"Game `{arguments['command']}` added.")
    return _back(request, guild_id)


async def reset_guild(request, guild_id: int):
    """Drop everything a server owns, so it inherits [DEFAULT] again.

    The guild row is shared with the rolls cog, so one confirmation drops both
    the games and the roll sets; two changes are logged so each cog reloads.
    """
    if (guild_id == DEFAULT_GUILD_ID):
        raise HTTPException(404, DEFAULT_CANNOT_BE_RESET)
    base = _base(request, guild_id)
    if (request.method == "GET"):
        return _page(request, guild_id, "confirm.html",
                     title="Remove this server's configuration",
                     message=("Every game and roll set this server configures "
                              "for itself is dropped: it goes back to "
                              "inheriting [DEFAULT], and its own changes are "
                              "lost."),
                     action_url=f"{base}/reset",
                     cancel_url=base)
    await pages.submitted(request)
    if (not await games_db_config.reset_guild_config(guild_id)):
        auth.flash(request, "info", "This server has no configuration of its own.")
        return _back(request, guild_id)
    await pages.log_change(request, guild_id, "game.reset", "[DEFAULT]")
    await pages.log_change(request, guild_id, "rollset.reset", "[DEFAULT]")
    auth.flash(request, "success",
               "This server now inherits the [DEFAULT] configuration.")
    return _back(request, guild_id)


async def adopt_game(request, guild_id: int):
    """Give a server its own copy of a game it inherits, then open it.

    Materializing copies the whole [DEFAULT] configuration; a game [DEFAULT]
    gained after the server already had a configuration is copied on its own.
    """
    if (guild_id == DEFAULT_GUILD_ID):
        raise HTTPException(404, DEFAULT_OWNS_EVERYTHING)
    form = await pages.submitted(request)
    command = form.get("command")
    command = command.strip() if isinstance(command, str) else ""
    game_id = await games_db_config.materialize_game(guild_id, command)
    if (game_id is None):
        raise HTTPException(404, NO_SUCH_GAME)
    await pages.log_change(request, guild_id, "game.adopt", command)
    auth.flash(request, "success",
               f"`{command}` is now part of this server's own configuration.")
    return _back_to_game(request, guild_id, game_id)


async def edit_game(request, guild_id: int):
    """Edit a game: every field at once, and its command on a rename."""
    game_id = int(request.path_params["game_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    if (request.method == "GET"):
        return _page(request, guild_id, "game_form.html", new=False, **detail)
    form = await pages.submitted(request)
    arguments, errors = forms.game_form(form, command, detail["commands"])
    changed = _changed(detail["game"], arguments, detail["api_values"])
    if (not errors and not changed and arguments["new_command"] is None):
        auth.flash(request, "info", f"Nothing changed in `{command}`.")
        return _back(request, guild_id)
    if (not errors and not await games_db_config.update_game(
            guild_id, command, new_command=arguments["new_command"],
            api_fields=arguments["api_fields"], **arguments["fields"])):
        errors.append(f"`{command}` is not configured for this server any more.")
    if (errors):
        detail["values"] = pages.submitted_values(form)
        detail["color_hex"] = forms.color_hex(form.get("color"))
        detail["color_set"] = bool(form.get("use_color"))
        detail["errors"] = errors
        return _page(request, guild_id, "game_form.html", new=False, **detail)
    if (arguments["new_command"] is not None):
        action = "game.rename"
        summary = f"{command} -> {arguments['new_command']}"
        done = f"Game `{command}` renamed to `{arguments['new_command']}`."
    else:
        action = "game.update"
        summary = ", ".join(changed)
        done = f"Game `{command}` updated: {summary}."
    await pages.log_change(request, guild_id, action, summary)
    auth.flash(request, "success", done)
    return _back(request, guild_id)


async def copy_game(request, guild_id: int):
    """Copy a game of the server, its parameters and overrides included."""
    game_id = int(request.path_params["game_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    values = {"command": "", "name": f"{detail['values']['name']} copy"}
    if (request.method == "GET"):
        return _page(request, guild_id, "game_copy.html", source=detail["game"],
                     game_id=game_id, values=values)
    form = await pages.submitted(request)
    arguments, errors = forms.copy_form(
        form, command, detail["values"]["name"] or command)
    if (not errors and not await games_db_config.copy_game(
            guild_id, command, arguments["command"], name=arguments["name"])):
        errors.append(f"`{arguments['command']}` is already configured here.")
    if (errors):
        return _page(request, guild_id, "game_copy.html", source=detail["game"],
                     game_id=game_id, values=pages.submitted_values(form),
                     errors=errors)
    await pages.log_change(request, guild_id, "game.copy",
               f"{arguments['command']} from {command}")
    auth.flash(request, "success",
               f"Game `{arguments['command']}` copied from `{command}`.")
    return _back(request, guild_id)


async def remove_game(request, guild_id: int):
    """Delete a game: a confirmation page in, a deletion out."""
    game_id = int(request.path_params["game_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    if (request.method == "GET"):
        return _page(request, guild_id, "confirm.html",
                     title=f"Remove {command}",
                     message=(f"Game `{command}` and its parameters are removed "
                              "from this configuration. This cannot be undone."),
                     action_url=(f"{_base(request, guild_id)}/games/{game_id}"
                                 "/remove"),
                     cancel_url=_base(request, guild_id))
    await pages.submitted(request)
    if (not await games_db_config.delete_game(guild_id, command)):
        raise HTTPException(404, NO_SUCH_GAME)
    await pages.log_change(request, guild_id, "game.remove", command)
    auth.flash(request, "success", f"Game `{command}` removed.")
    return _back(request, guild_id)


async def new_parameter(request, guild_id: int):
    """Add a parameter to a game."""
    game_id = int(request.path_params["game_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    values = _parameter_values({})
    if (request.method == "GET"):
        return _page(request, guild_id, "parameter_form.html", new=True,
                     game=detail["game"], game_id=game_id, values=values)
    form = await pages.submitted(request)
    arguments, errors = forms.parameter_form(form)
    if (not errors and not await games_db_config.add_parameter(
            guild_id, command, arguments["name"], arguments["values"],
            api_field=arguments["api_field"],
            display_name=arguments["display_name"])):
        errors.append(f"`{arguments['name']}` is already a parameter of "
                      f"`{command}`.")
    if (errors):
        return _page(request, guild_id, "parameter_form.html", new=True,
                     game=detail["game"], game_id=game_id,
                     values=_parameter_values(pages.submitted_values(form)),
                     errors=errors)
    await pages.log_change(request, guild_id, "parameter.add",
               f"{command}/{arguments['name']}")
    auth.flash(request, "success",
               f"Parameter `{arguments['name']}` added to `{command}`.")
    return _back_to_game(request, guild_id, game_id)


async def edit_parameter(request, guild_id: int):
    """Change a parameter's accepted values, its label, or its API field."""
    game_id = int(request.path_params["game_id"])
    parameter_id = int(request.path_params["parameter_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    current = _parameter(detail, parameter_id)
    name = current["name"]
    if (request.method == "GET"):
        return _page(request, guild_id, "parameter_form.html", new=False,
                     game=detail["game"], game_id=game_id,
                     values=_parameter_values(current, parameter_id))
    form = await pages.submitted(request)
    arguments, errors = forms.parameter_form(form)
    if (not errors and not await games_db_config.update_parameter(
            guild_id, command, name, values=arguments["values"],
            api_field=arguments["api_field"],
            display_name=arguments["display_name"])):
        errors.append(f"`{name}` is not a parameter of `{command}` any more.")
    if (errors):
        return _page(request, guild_id, "parameter_form.html", new=False,
                     game=detail["game"], game_id=game_id,
                     values=_parameter_values(pages.submitted_values(form),
                                              parameter_id),
                     errors=errors)
    await pages.log_change(request, guild_id, "parameter.update", f"{command}/{name}")
    auth.flash(request, "success", f"Parameter `{name}` updated.")
    return _back_to_game(request, guild_id, game_id)


async def remove_parameter(request, guild_id: int):
    """Delete a parameter: a confirmation page in, a deletion out."""
    game_id = int(request.path_params["game_id"])
    parameter_id = int(request.path_params["parameter_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    name = _parameter(detail, parameter_id)["name"]
    if (request.method == "GET"):
        return _page(request, guild_id, "confirm.html",
                     title=f"Remove {name}",
                     message=(f"Parameter `{name}` is removed from `{command}`, "
                              "and every value it accepts with it."),
                     action_url=(f"{_base(request, guild_id)}/games/{game_id}"
                                 f"/parameters/{parameter_id}/remove"),
                     cancel_url=f"{_base(request, guild_id)}/games/{game_id}")
    await pages.submitted(request)
    if (not await games_db_config.delete_parameter(guild_id, command, name)):
        raise HTTPException(404, NO_SUCH_PARAMETER)
    await pages.log_change(request, guild_id, "parameter.remove", f"{command}/{name}")
    auth.flash(request, "success",
               f"Parameter `{name}` removed from `{command}`.")
    return _back_to_game(request, guild_id, game_id)


async def set_ping(request, guild_id: int):
    """Set the role or the member a game pings, on its own page."""
    game_id = int(request.path_params["game_id"])
    detail = await _ping_detail(request, guild_id, game_id)
    command = detail["game"].command
    if (request.method == "GET"):
        return _ping_page(request, guild_id, detail)
    form = await pages.submitted(request)
    values, errors = forms.ping_form(form)
    if (not errors and values.get("name")):
        values, errors = await _named_member(request, guild_id, values["name"])
    if (not errors and not await games_db_config.update_game(
            guild_id, command, role=values["mention"])):
        errors.append(f"`{command}` is not configured for this server any more.")
    if (errors):
        detail["errors"] = errors
        detail["values"] = pages.submitted_values(form)
        return _ping_page(request, guild_id, detail)
    await pages.log_change(request, guild_id, "game.ping", command)
    auth.flash(request, "success", f"Ping updated for `{command}`.")
    return _back(request, guild_id)


async def clear_ping(request, guild_id: int):
    """Clear a game's ping."""
    game_id = int(request.path_params["game_id"])
    detail = await _ping_detail(request, guild_id, game_id)
    command = detail["game"].command
    await pages.submitted(request)
    if (not await games_db_config.update_game(guild_id, command, role="")):
        raise HTTPException(404, NO_SUCH_GAME)
    await pages.log_change(request, guild_id, "game.ping.clear", command)
    auth.flash(request, "success", f"Ping cleared for `{command}`.")
    return _back(request, guild_id)


async def _ping_detail(request, guild_id: int, game_id: int) -> dict:
    """One game's ping page context, or a 404 when the server has no such row."""
    detail = await overview.ping_detail(request.app.state.discord, guild_id,
                                        game_id)
    if (detail is None):
        raise HTTPException(404, NO_SUCH_GAME)
    return detail


def _ping_page(request, guild_id: int, detail: dict):
    """Render the ping page, with whatever the last submission carried."""
    detail.setdefault("values", {"role": "", "member": ""})
    detail.setdefault("errors", [])
    return _page(request, guild_id, "ping_form.html", **detail)


async def _named_member(request, guild_id: int,
                        name: str) -> tuple[dict, list[str]]:
    """The mention of the member a typed name matches, or an error to show.

    Discord's own member search answers: a member list is never read, so a
    server of any size costs the same. An exact name wins, otherwise the first
    suggestion is taken, as the bot's own admin does.
    """
    try:
        members = await request.app.state.discord.member_search(guild_id, name)
    except discord_reads.UNREACHABLE:
        return {}, ["Discord could not be reached: try again."]
    if (not members):
        return {}, [f"No member of this server matches `{name}`."]
    return {"mention": members[0]["id"]}, []


async def set_token(request, guild_id: int):
    """Set a game's API token on its own page; the value is never shown again."""
    game_id = int(request.path_params["game_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    if (request.method == "GET"):
        return _page(request, guild_id, "token_form.html", game=detail["game"],
                     game_id=game_id)
    token, errors = forms.token_form(await pages.submitted(request))
    if (not errors and not await games_db_config.update_game(
            guild_id, command, api_token=token)):
        errors.append(f"`{command}` is not configured for this server any more.")
    if (errors):
        return _page(request, guild_id, "token_form.html", game=detail["game"],
                     game_id=game_id, errors=errors)
    await pages.log_change(request, guild_id, "game.token", command)
    auth.flash(request, "success", f"API token set for `{command}`.")
    return _back(request, guild_id)


async def clear_token(request, guild_id: int):
    """Clear a game's API token."""
    game_id = int(request.path_params["game_id"])
    detail = await _detail(request, guild_id, game_id)
    command = detail["game"].command
    await pages.submitted(request)
    if (not await games_db_config.update_game(guild_id, command, api_token="")):
        raise HTTPException(404, NO_SUCH_GAME)
    await pages.log_change(request, guild_id, "game.token.clear", command)
    auth.flash(request, "success", f"API token cleared for `{command}`.")
    return _back(request, guild_id)


NO_SUCH_GAME = "This server has no such game."
NO_SUCH_PARAMETER = "This game has no such parameter."
DEFAULT_OWNS_EVERYTHING = ("The [DEFAULT] configuration already owns every "
                           "game: there is nothing to copy.")
DEFAULT_CANNOT_BE_RESET = ("The [DEFAULT] configuration is the one servers "
                           "inherit: it cannot be dropped.")


async def _detail(request, guild_id: int, game_id: int) -> dict:
    """One game's page detail, or a 404 when the server has no such game row."""
    detail = await overview.game_detail(request.app.state.discord, guild_id,
                                        game_id)
    if (detail is None):
        raise HTTPException(404, NO_SUCH_GAME)
    return detail


def _parameter(detail: dict, parameter_id: int) -> dict:
    """The parameter row a page edits, or a 404 when the game has none."""
    for parameter in detail["parameters"]:
        if (parameter["id"] == parameter_id):
            return parameter
    raise HTTPException(404, NO_SUCH_PARAMETER)


def _parameter_values(source: dict, parameter_id: int | None = None) -> dict:
    """The values a parameter form reads, from a row or from a submitted form.

    A row keys the accepted values ``values_text`` and a submitted form keys
    them after its field (``values``); the form reads the one name either way.
    ``parameter_id`` is the row being edited, when the page edits one.
    """
    return {
        "id": parameter_id,
        "name": source.get("name", ""),
        "display_name": source.get("display_name", ""),
        "values_text": source.get("values_text", source.get("values", "")),
        "api_field": source.get("api_field", ""),
    }


def _changed(game, arguments: dict, stored_api: dict[str, str]) -> list[str]:
    """The field names a full-value form actually changed, sorted."""
    changed = [name for name, value in arguments["fields"].items()
               if _comparable(getattr(game, name, None)) != _comparable(value)]
    posted = arguments["api_fields"]
    if (any(_comparable(stored_api.get(argument)) != _comparable(posted.get(key))
            for argument, key in validation.API_FIELD_ARGUMENTS.items())):
        changed.append("api_fields")
    return sorted(changed)


def _comparable(value) -> str:
    """A stored or a posted value, with every empty form spelled alike."""
    return "" if value is None else str(value)


def _page(request, guild_id: int, template: str, **context):
    """Render a game page with the values every page of the section carries."""
    context.setdefault("errors", [])
    return pages.render(request, template, active=_active(guild_id),
                        game_base=_base(request, guild_id),
                        guild_id=guild_id, **context)


def _active(guild_id: int) -> str:
    """The nav item a game page belongs to."""
    return "ops_default" if (guild_id == DEFAULT_GUILD_ID) else "guild"


def _base(request, guild_id: int) -> str:
    """The URL a game page hangs its form actions and its links off."""
    if (guild_id == DEFAULT_GUILD_ID):
        return str(request.url_for("ops_default"))
    return str(request.url_for("guild", guild_id=guild_id))


def _back(request, guild_id: int) -> RedirectResponse:
    """Send the manager back to the page of the server they edited."""
    return RedirectResponse(_base(request, guild_id), status_code=303)


def _back_to_game(request, guild_id: int, game_id: int) -> RedirectResponse:
    """Send the manager back to the page of the game they edited."""
    return RedirectResponse(f"{_base(request, guild_id)}/games/{game_id}",
                            status_code=303)


# The game pages, as ``(path, route name, view, methods)``. Each one is served
# for a single server and, under ``/ops/default``, for the [DEFAULT] config.
# A game and a parameter are named by their row id, never by their command or
# name: a command literally called "new" would otherwise be the add page.
PAGES = (
    ("games/new", "game_new", new_game, ["GET", "POST"]),
    ("reset", "guild_reset", reset_guild, ["GET", "POST"]),
    ("games/adopt", "game_adopt", adopt_game, ["POST"]),
    ("games/{game_id:int}", "game_edit", edit_game, ["GET", "POST"]),
    ("games/{game_id:int}/copy", "game_copy", copy_game, ["GET", "POST"]),
    ("games/{game_id:int}/remove", "game_remove", remove_game, ["GET", "POST"]),
    ("games/{game_id:int}/parameters/new", "parameter_new", new_parameter,
     ["GET", "POST"]),
    ("games/{game_id:int}/parameters/{parameter_id:int}", "parameter_edit",
     edit_parameter, ["GET", "POST"]),
    ("games/{game_id:int}/parameters/{parameter_id:int}/remove",
     "parameter_remove", remove_parameter, ["GET", "POST"]),
    ("games/{game_id:int}/ping", "game_ping", set_ping, ["GET", "POST"]),
    ("games/{game_id:int}/ping/clear", "game_ping_clear", clear_ping,
     ["POST"]),
    ("games/{game_id:int}/token", "game_token", set_token, ["GET", "POST"]),
    ("games/{game_id:int}/token/clear", "game_token_clear", clear_token,
     ["POST"]),
)


