"""The Discord login, the session it opens, and the gates a page goes through."""

import secrets
from functools import wraps
from typing import Any

import aiohttp
import discord
from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse

from . import settings

# The bot's own Discord application, which the login authorizes.
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
API_BASE_URL = "https://discord.com/api/v10"
SCOPES = ("identify", "guilds")
REQUEST_TIMEOUT = 10
# manage_guild | administrator: the two permission bits that allow editing.
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8
# What a request of the login flow raises when it fails or cannot be trusted.
UNREACHABLE = (aiohttp.ClientError, KeyError, ValueError, TimeoutError)

# Session keys: the account, the servers it may edit, the OAuth state, flashes.
USER_KEY = "user"
GUILDS_KEY = "guilds"
STATE_KEY = "oauth_state"
FLASH_KEY = "flash"

NO_SUCH_GUILD = "This server is not one of yours."
NOT_AN_OPERATOR = "Only the panel's operators can open this page."


def session_user(request) -> dict | None:
    """The logged in Discord account, or None when nobody is logged in."""
    return request.session.get(USER_KEY)


def session_guilds(request) -> dict[str, str]:
    """The servers the logged in account may edit, as ``{guild_id: name}``."""
    return request.session.get(GUILDS_KEY) or {}


def session_guild_name(request, guild_id: int) -> str | None:
    """The name of a server the account may edit, or None when it may not."""
    return session_guilds(request).get(str(guild_id))


def is_operator(user_id: int | None) -> bool:
    """Whether an account may edit every server (see OPERATOR_DISCORD_IDS)."""
    return user_id is not None and user_id in settings.OPERATOR_DISCORD_IDS


def is_operator_request(request) -> bool:
    """Whether the logged in account may edit every server."""
    return is_operator((session_user(request) or {}).get("id"))


def can_edit(request, guild_id: int) -> bool:
    """Whether the account may edit a server: an operator, or a manager of it."""
    return (is_operator_request(request)
            or session_guild_name(request, guild_id) is not None)


def require_editable(request, guild_id: int) -> None:
    """Refuse a server the account may not edit, as if it did not exist."""
    if (not can_edit(request, guild_id)):
        raise HTTPException(404, NO_SUCH_GUILD)


def redirect_uri(request) -> str:
    """The callback Discord sends the visitor back to."""
    if (settings.DISCORD_REDIRECT_URI):
        return settings.DISCORD_REDIRECT_URI
    if (settings.BASE_URL):
        return f"{settings.BASE_URL}/discord/callback"
    return str(request.url_for("discord_callback"))


def authorize_url(redirect_uri: str, state: str) -> str:
    """The Discord URL the login sends the visitor to."""
    return discord.utils.oauth_url(
        settings.DISCORD_CLIENT_ID, redirect_uri=redirect_uri,
        scopes=SCOPES, state=state)


async def fetch_identity(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for its account and that account's guilds.

    Raises anything in ``UNREACHABLE`` when Discord refuses or is unreachable.
    """
    token = await _request(
        "POST", f"{API_BASE_URL}/oauth2/token",
        data={
            "client_id": settings.DISCORD_CLIENT_ID,
            "client_secret": settings.DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        })
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    account = await _request("GET", f"{API_BASE_URL}/users/@me", headers=headers)
    guilds = await _request("GET", f"{API_BASE_URL}/users/@me/guilds",
                            headers=headers)
    return {"account": account, "guilds": guilds}


async def _request(method: str, url: str, **kwargs) -> Any:
    """Make one request of the login flow, raising ``UNREACHABLE`` on failure."""
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, **kwargs) as response:
            response.raise_for_status()
            return await response.json()


def store_identity(request, identity: dict) -> None:
    """Keep the account and the servers it may edit in the session.

    Only the servers Discord lets the account manage are kept: a page may edit
    nothing else, and the session cookie stays small.
    """
    account = identity["account"]
    request.session[USER_KEY] = {
        "id": int(account["id"]),
        "name": (account.get("global_name") or account.get("username") or ""),
    }
    request.session[GUILDS_KEY] = {
        str(guild["id"]): guild.get("name") or str(guild["id"])
        for guild in identity["guilds"]
        if int(guild.get("permissions") or 0)
        & (MANAGE_GUILD | ADMINISTRATOR)
    }


def new_state(request) -> str:
    """Start a login: remember a fresh state token and return it."""
    state = secrets.token_urlsafe(32)
    request.session[STATE_KEY] = state
    return state


def check_state(request, submitted: str | None) -> bool:
    """Whether a callback carries the state this session was given."""
    expected = request.session.pop(STATE_KEY, None)
    if (not expected or not submitted):
        return False
    return secrets.compare_digest(str(expected), str(submitted))


def logout(request) -> None:
    """Forget the login."""
    request.session.clear()


def flash(request, level: str, message: str) -> None:
    """Queue a message for the next page the visitor sees."""
    queued = list(request.session.get(FLASH_KEY) or [])
    queued.append([level, message])
    request.session[FLASH_KEY] = queued


def pop_flashes(request) -> list[tuple[str, str]]:
    """Take the messages queued for this page."""
    return [tuple(queued) for queued in request.session.pop(FLASH_KEY, [])]


def _login_redirect(request) -> RedirectResponse | None:
    """Send an unauthenticated visitor to the login page."""
    if (session_user(request) is not None):
        return None
    return RedirectResponse(request.url_for("login"), status_code=303)


def require_login(view):
    """Gate a page behind the Discord login."""
    @wraps(view)
    async def wrapper(request, **kwargs):
        redirect = _login_redirect(request)
        if (redirect is not None):
            return redirect
        return await view(request, **kwargs)
    return wrapper


def _path_guild_id(request) -> int:
    """The server id in the requested path, which the gate reads."""
    try:
        return int(request.path_params["guild_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(404, NO_SUCH_GUILD)


def require_guild(view):
    """Gate a page about one server, which the account must be able to edit."""
    @wraps(view)
    async def wrapper(request, **kwargs):
        redirect = _login_redirect(request)
        if (redirect is not None):
            return redirect
        require_editable(request, _path_guild_id(request))
        return await view(request, **kwargs)
    return wrapper


def require_operator(view):
    """Gate a page only the panel's operators may open."""
    @wraps(view)
    async def wrapper(request, **kwargs):
        redirect = _login_redirect(request)
        if (redirect is not None):
            return redirect
        if (not is_operator_request(request)):
            raise HTTPException(403, NOT_AN_OPERATOR)
        return await view(request, **kwargs)
    return wrapper
