"""A CSRF token kept in the session and required on every changing request."""

import hmac
import secrets

SESSION_KEY = "csrf"
FORM_FIELD = "csrf_token"


def token(request) -> str:
    """The session's CSRF token, created on first use."""
    if (SESSION_KEY not in request.session):
        request.session[SESSION_KEY] = secrets.token_urlsafe(32)
    return request.session[SESSION_KEY]


def is_valid(request, submitted: str | None) -> bool:
    """Whether a submitted token is the one this session was given."""
    expected = request.session.get(SESSION_KEY)
    if (not expected or not submitted):
        return False
    return hmac.compare_digest(str(expected), str(submitted))
