"""The ASGI app of the panel: middleware, routes, and the database lifespan."""

from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from tortoise import Tortoise

from db.orm_config import orm_config

from . import discord_reads, errors, settings
from .views import games, home, ops, rollsets

STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE = "lfgbot_admin"


@asynccontextmanager
async def lifespan(app):
    """Hold what the pages read, for the life of the web process.

    The database is opened with the global fallback, which is what lets a
    request's queries run: a request runs in a task of its own, which does not
    inherit the context opened here. The Discord client logs in for its REST
    reads and never connects to the gateway, so the bot keeps its session.
    """
    await Tortoise.init(config=orm_config(settings.database_url()),
                        _enable_global_fallback=True)
    await app.state.discord.open()
    print(settings.startup_summary())
    try:
        yield
    finally:
        await app.state.discord.close()
        await Tortoise.close_connections()


def middleware() -> list[Middleware]:
    """The middleware stack, outermost first: hosts, HTTPS, then the session."""
    stack: list[Middleware] = []
    if (settings.TRUSTED_HOSTS):
        stack.append(Middleware(TrustedHostMiddleware,
                                allowed_hosts=settings.TRUSTED_HOSTS))
    if (settings.force_https()):
        stack.append(Middleware(HTTPSRedirectMiddleware))
    stack.append(Middleware(SessionMiddleware,
                            secret_key=settings.secret_key(),
                            session_cookie=SESSION_COOKIE,
                            max_age=settings.SESSION_MAX_AGE,
                            same_site="lax",
                            https_only=settings.force_https()))
    return stack


def routes() -> list:
    """Every page of the panel."""
    return [
        Route("/healthz", home.healthz),
        Route("/", home.home, name="home"),
        Route("/login", home.login_page, name="login"),
        Route("/logout", home.logout, methods=["POST"], name="logout"),
        Route("/discord/connect", home.discord_connect, name="discord_connect"),
        Route("/discord/callback", home.discord_callback,
              name="discord_callback"),
        Route("/g/{guild_id:int}", home.guild_page, name="guild"),
        *games.routes(),
        *rollsets.routes(),
        Route("/ops", ops.hub, name="ops"),
        Route("/ops/default", ops.default_page, name="ops_default"),
        Route("/ops/changes", ops.changes_page, name="ops_changes"),
        Route("/ops/changes/prune", ops.prune_page,
              methods=["GET", "POST"], name="ops_prune"),
        Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
    ]


def create_app() -> Starlette:
    """Build the app; settings are read here rather than at import time."""
    app = Starlette(
        routes=routes(), middleware=middleware(), lifespan=lifespan,
        exception_handlers={403: errors.http_error, 404: errors.http_error,
                            405: errors.http_error, 500: errors.http_error})
    # One Discord client and cache per process, owned by the app; the lifespan
    # logs the client in and out.
    app.state.discord = discord_reads.DiscordReads()
    return app


app = create_app()
