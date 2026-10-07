"""Tests for the panel's Discord login, its session and its page gates."""

from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from starlette.testclient import TestClient

from admin_web import auth, settings

from tests.conftest import (WEB_SESSION_COOKIE, WEB_SESSION_SECRET, be_operator,
                            csrf_of, read_session)

ACCOUNT = {"id": "42", "username": "manager", "global_name": "Manager"}


def location_path(response) -> str:
    """The path a redirect points at (the panel builds absolute URLs)."""
    return urlsplit(response.headers["location"]).path


def _identity(*guilds: tuple[int, str, int]) -> dict:
    """The identity Discord reports: the account, and ``(id, name, perms)`` guilds."""
    return {
        "account": ACCOUNT,
        "guilds": [{"id": str(guild_id), "name": name,
                    "permissions": str(permissions)}
                   for guild_id, name, permissions in guilds],
    }


class TestLogin:
    """The Discord login: the button, the redirect, and the callback."""

    def test_the_login_page_offers_discord(self, client):
        response = client.get("/login")
        assert response.status_code == 200
        assert "/discord/connect" in response.text

    def test_the_login_page_says_when_login_is_unconfigured(self, client,
                                                            monkeypatch):
        monkeypatch.setattr(settings, "DISCORD_CLIENT_ID", "")
        assert "not configured" in client.get("/login").text

    def test_connecting_sends_the_visitor_to_discord(self, client):
        response = client.get("/discord/connect", follow_redirects=False)
        assert response.status_code == 302
        target = urlsplit(response.headers["location"])
        assert f"{target.scheme}://{target.netloc}{target.path}" == (
            auth.AUTHORIZE_URL)
        query = parse_qs(target.query)
        assert query["client_id"] == ["test-client"]
        assert query["scope"] == ["identify guilds"]
        # The state is remembered, so the callback can check it.
        assert read_session(client)["oauth_state"] == query["state"][0]

    def test_connecting_is_refused_when_login_is_unconfigured(self, client,
                                                              monkeypatch):
        monkeypatch.setattr(settings, "DISCORD_CLIENT_SECRET", "")
        response = client.get("/discord/connect", follow_redirects=False)
        assert response.status_code == 404

    def test_the_callback_keeps_only_the_servers_it_may_manage(self, client,
                                                               monkeypatch):
        client.get("/discord/connect", follow_redirects=False)
        state = read_session(client)["oauth_state"]
        identity = _identity((7, "Server Seven", auth.MANAGE_GUILD),
                            (8, "Plain Server", 0))

        async def fetched(code, redirect_uri):
            return identity

        monkeypatch.setattr(auth, "fetch_identity", fetched)
        response = client.get("/discord/callback",
                              params={"code": "abc", "state": state},
                              follow_redirects=False)
        assert response.status_code == 303
        assert location_path(response) == "/"
        session = read_session(client)
        assert session["user"] == {"id": 42, "name": "Manager"}
        assert session["guilds"] == {"7": "Server Seven"}

    def test_a_callback_with_a_stale_state_is_refused(self, client, monkeypatch):
        client.get("/discord/connect", follow_redirects=False)

        async def never(code, redirect_uri):
            raise AssertionError("the identity must not be fetched")

        monkeypatch.setattr(auth, "fetch_identity", never)
        response = client.get("/discord/callback",
                              params={"code": "abc", "state": "stale"},
                              follow_redirects=False)
        assert response.status_code == 303
        assert location_path(response) == "/login"
        assert "user" not in read_session(client)

    def test_a_cancelled_login_is_reported(self, client):
        response = client.get("/discord/callback",
                              params={"error": "access_denied"})
        assert "cancelled" in response.text

    def test_an_unreachable_discord_is_reported(self, client, monkeypatch):
        client.get("/discord/connect", follow_redirects=False)
        state = read_session(client)["oauth_state"]

        async def boom(code, redirect_uri):
            raise aiohttp.ClientError("no route to host")

        monkeypatch.setattr(auth, "fetch_identity", boom)
        response = client.get("/discord/callback",
                              params={"code": "abc", "state": state})
        assert "could not be reached" in response.text

    def test_logging_out_needs_the_session_token(self, client, login):
        login(client, guilds={7: "Server Seven"})
        assert client.post("/logout", data={"csrf_token": "wrong"}).status_code == 403
        assert read_session(client)["user"]["id"] == 42

    def test_logging_out_forgets_the_login(self, client, login):
        login(client, guilds={7: "Server Seven"})
        response = client.post("/logout", data={"csrf_token": csrf_of(client)},
                               follow_redirects=False)
        assert response.status_code == 303
        assert "user" not in read_session(client)


class TestGates:
    """Who may open what."""

    @pytest.mark.parametrize("path", ["/", "/g/7", "/ops", "/ops/changes",
                                      "/ops/changes/prune", "/ops/default"])
    def test_a_visitor_without_a_session_is_sent_to_the_login(self, client, path):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert location_path(response) == "/login"

    def test_a_server_the_session_does_not_know_is_not_found(self, client, login):
        login(client, guilds={7: "Server Seven"})
        assert client.get("/g/8").status_code == 404

    def test_the_home_page_lists_the_servers_of_the_session(self, client, login):
        login(client, guilds={7: "Server Seven", 8: "Alpha Server"})
        response = client.get("/")
        assert response.status_code == 200
        assert response.text.index("Alpha Server") < response.text.index(
            "Server Seven")

    def test_a_manager_may_not_open_the_operations_pages(self, client, login):
        login(client, guilds={7: "Server Seven"})
        for path in ("/ops", "/ops/changes", "/ops/changes/prune",
                     "/ops/default"):
            assert client.get(path).status_code == 403, path

    def test_an_operator_may_open_every_server(self, client, login, monkeypatch,
                                              db):
        be_operator(monkeypatch)
        login(client)
        assert client.get("/ops").status_code == 200
        assert client.get("/g/999").status_code == 200
        assert client.get("/ops/default").status_code == 200

class TestLifespan:
    """What the app opens for the life of the web process."""

    async def test_it_opens_the_database_and_the_discord_reads(self, monkeypatch):
        # A request runs in a task of its own, which does not inherit the
        # context the lifespan opened: without the database's global fallback,
        # every query of a page would fail. The Discord client is opened as
        # well, so the pages can show channel and role names.
        from admin_web import app as panel

        # The lifespan needs a URL, not a database: Tortoise.init is stubbed
        # below, so this one is never dialled. Pinning it here keeps the test
        # off DATABASE_URL, which only a deployment sets.
        monkeypatch.setattr(settings, "DATABASE_URL",
                            "postgres://test:test@localhost/test")

        called = {}

        async def fake_init(*args, **kwargs):
            called.update(kwargs)

        async def fake_close():
            called["database_closed"] = True

        async def fake_open_client():
            called["discord_open"] = True

        async def fake_close_client():
            called["discord_closed"] = True

        monkeypatch.setattr(panel.Tortoise, "init", fake_init)
        monkeypatch.setattr(panel.Tortoise, "close_connections", fake_close)
        monkeypatch.setattr(panel.app.state.discord, "open", fake_open_client)
        monkeypatch.setattr(panel.app.state.discord, "close",
                            fake_close_client)
        async with panel.lifespan(panel.app):
            assert called["_enable_global_fallback"] is True
            assert called["discord_open"] is True
        assert called["database_closed"] is True
        assert called["discord_closed"] is True

    def test_healthz_answers_ok(self, client, db):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.text == "ok"

    def test_healthz_reports_a_database_it_cannot_read(self, client, monkeypatch,
                                                       db):
        from db import models

        def boom(*args, **kwargs):
            raise RuntimeError("database is down")

        monkeypatch.setattr(models.Guild, "all", staticmethod(boom))
        response = client.get("/healthz")
        assert response.status_code == 503
        assert "unreachable" in response.text


class TestTheErrorPage:
    """The panel's error page renders whatever went wrong, layout included."""

    @staticmethod
    def _signed(payload: bytes) -> str:
        """A session cookie carrying ``payload``, signed as the panel signs one."""
        from base64 import b64encode

        from itsdangerous import TimestampSigner

        return TimestampSigner(WEB_SESSION_SECRET).sign(
            b64encode(payload)).decode()

    def test_a_session_cookie_that_cannot_be_read_still_renders_a_page(
            self, web_app):
        # A signed cookie that is not a session makes the middleware raise
        # before it puts one in the scope: the error page then has to render
        # from the values the layout needs and the session cannot provide.
        client = TestClient(web_app, raise_server_exceptions=False)
        client.cookies.set(WEB_SESSION_COOKIE, self._signed(b"not a session"))
        response = client.get("/")
        assert response.status_code == 500
        assert "LFG Bot" in response.text
        assert "went wrong" in response.text

