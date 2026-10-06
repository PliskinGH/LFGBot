"""Environment settings of the web panel."""

import os
import secrets

from dotenv import load_dotenv

# The file wins over the environment: a stale or empty variable left in a shell
# would otherwise shadow what .env says (load_dotenv does not override by
# default). Deployed hosts have no .env, so their variables are read as they are.
load_dotenv(override=True)

# Where the panel is reachable, when it is not the request's own host.
BASE_URL = os.getenv("WEB_BASE_URL", "").rstrip("/")

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
# Registered in the Discord application; rebuilt from the request when unset.
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
SESSION_SECRET = os.getenv("SESSION_SECRET")
# Hostnames the panel answers for; empty accepts every one.
TRUSTED_HOSTS = [host for host in os.getenv("TRUSTED_HOSTS", "").split() if host]
# How long a login lasts before Discord has to be asked again.
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE") or 8 * 60 * 60)
# Discord ids allowed to edit every server, and the [DEFAULT] configuration.
OPERATOR_DISCORD_IDS = frozenset(
    int(value) for value in
    (os.getenv("OPERATOR_DISCORD_IDS") or "").replace(";", " ").replace(",", " ").split()
    if value.strip().isdigit())

OAUTH_DISABLED_MESSAGE = ("Discord login is not configured: set "
                          "DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET.")


def oauth_configured() -> bool:
    """Whether the Discord application credentials are set."""
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)


def secret_key() -> str:
    """The cookie signing key: SESSION_SECRET, or a throwaway one."""
    if (SESSION_SECRET):
        return SESSION_SECRET
    # A throwaway key logs everyone out on restart; production sets the var.
    print("SESSION_SECRET is not set: logins will not survive a restart.")
    return secrets.token_urlsafe(32)


def database_url() -> str:
    """The database the panel reads, refusing to serve without one."""
    if (not DATABASE_URL):
        raise RuntimeError(
            "DATABASE_URL is required: the panel edits the database "
            "configuration only, so config-file mode has nothing to edit.")
    return DATABASE_URL


def force_https() -> bool:
    """Whether requests must arrive over HTTPS (the host's proxy terminates it)."""
    return BASE_URL.startswith("https://")


def startup_summary() -> str:
    """One line saying what the panel runs with, secrets left out."""
    if (oauth_configured()):
        login = "Discord login configured"
    else:
        login = ("Discord login NOT configured: DISCORD_CLIENT_ID and/or "
                 "DISCORD_CLIENT_SECRET are empty")
    return (f"Web panel: {login}; {len(OPERATOR_DISCORD_IDS)} operator(s); "
            f"base URL {BASE_URL or '(from the request)'}.")
