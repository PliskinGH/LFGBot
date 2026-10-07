"""The page loop and gates a configuration section's pages share.

Both sections (games, roll sets) are served for one server under ``/g/{id}``
and, for the ``[DEFAULT]`` configuration, under ``/ops/default``; the gates and
the URL shape are identical, so they live here once.
"""

from functools import wraps

from starlette.routing import Route

from cogs.matchmaking.constants import DEFAULT_GUILD_ID

from .. import auth


def routes(pages) -> list[Route]:
    """The pages of a section, for one server and for [DEFAULT].

    ``pages`` is an iterable of ``(path, route name, view, methods)``.
    """
    routes: list[Route] = []
    for path, name, view, methods in pages:
        routes.append(Route(f"/g/{{guild_id:int}}/{path}", _guild_view(view),
                            methods=methods, name=name))
        routes.append(Route(f"/ops/default/{path}", _default_view(view),
                            methods=methods, name=f"ops_{name}"))
    return routes


def _guild_view(view):
    """A page of one server: its managers, or an operator."""
    return auth.require_guild(_with_guild(view, from_path=True))


def _default_view(view):
    """A page of the [DEFAULT] configuration: the panel's operators only."""
    return auth.require_operator(_with_guild(view, from_path=False))


def _with_guild(view, from_path: bool):
    """A page function taking the guild it edits, as the gates supply it."""
    @wraps(view)
    async def page(request, **kwargs):
        guild_id = (int(request.path_params["guild_id"]) if from_path
                    else DEFAULT_GUILD_ID)
        return await view(request, guild_id)
    return page
