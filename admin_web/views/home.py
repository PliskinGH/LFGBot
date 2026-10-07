"""The pages of the panel itself: the login, the server picker and one server."""

from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, RedirectResponse

from db import models

from .. import auth, csrf, overview, pages, settings


async def healthz(request):
    """Answer the host's health check, database included."""
    try:
        await models.Guild.all().count()
    except Exception as error:
        return PlainTextResponse(f"database unreachable: {error}", status_code=503)
    return PlainTextResponse("ok")


async def login_page(request):
    """Offer the Discord login, or say why it cannot be offered."""
    if (auth.session_user(request) is not None):
        return RedirectResponse(request.url_for("home"), status_code=303)
    return pages.render(request, "login.html",
                        configured=settings.oauth_configured())


async def discord_connect(request):
    """Send the visitor to Discord to authorize the panel."""
    if (not settings.oauth_configured()):
        raise HTTPException(404, settings.OAUTH_DISABLED_MESSAGE)
    state = auth.new_state(request)
    return RedirectResponse(
        auth.authorize_url(auth.redirect_uri(request), state),
        status_code=302)


async def discord_callback(request):
    """Finish the login: check the callback, then keep the account in the session."""
    if (not settings.oauth_configured()):
        raise HTTPException(404, settings.OAUTH_DISABLED_MESSAGE)
    query = request.query_params
    if (query.get("error")):
        return _login_again(request, "Discord login was cancelled.")
    if (not query.get("code")
            or not auth.check_state(request, query.get("state"))):
        return _login_again(request, "Discord login failed: try again.")
    try:
        identity = await auth.fetch_identity(
            query["code"], auth.redirect_uri(request))
    except auth.UNREACHABLE:
        return _login_again(request, "Discord could not be reached: try again.")
    auth.store_identity(request, identity)
    return RedirectResponse(request.url_for("home"), status_code=303)


def _login_again(request, message: str) -> RedirectResponse:
    """Send the visitor back to the login page with a reason."""
    auth.flash(request, "error", message)
    return RedirectResponse(request.url_for("login"), status_code=303)


async def logout(request):
    """Forget the login (a POST, so no link can log the visitor out)."""
    form = await request.form()
    if (not csrf.is_valid(request, form.get(csrf.FORM_FIELD))):
        raise HTTPException(403, "The form was not sent by this session: try again.")
    auth.logout(request)
    return RedirectResponse(request.url_for("home"), status_code=303)


@auth.require_login
async def home(request):
    """The landing page: the servers the logged in account may edit."""
    guilds = sorted(auth.session_guilds(request).items(),
                    key=lambda item: item[1].lower())
    return pages.render(request, "home.html", active="home",
                        guilds=[{"id": int(guild_id), "name": name}
                                for guild_id, name in guilds])


@auth.require_guild
async def guild_page(request):
    """One server: what it has configured, and what changed recently."""
    guild_id = int(request.path_params["guild_id"])
    reads = request.app.state.discord
    # The session names the servers the account manages; an operator reads the
    # rest from Discord, and the id is only the last resort.
    name = (auth.session_guild_name(request, guild_id)
            or await reads.name_of(guild_id)
            or str(guild_id))
    return pages.render(request, "guild.html", active="guild",
                        guild_id=guild_id, guild_name=name, is_default=False,
                        game_base=str(request.url_for("guild",
                                                      guild_id=guild_id)),
                        **await overview.guild_config(reads, guild_id))
