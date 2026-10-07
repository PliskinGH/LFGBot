"""Filling in the templates: the one place that knows what every page carries."""

from pathlib import Path

from jinja2 import StrictUndefined
from starlette.templating import Jinja2Templates

from . import auth, csrf

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
# A page's variables are supplied explicitly: a missing one raises instead of
# rendering an empty value (an empty href, say, reloads the page it is on).
templates.env.undefined = StrictUndefined


def render(request, template: str, status_code: int = 200, **context):
    """Render a page with the values every page carries.

    The CSRF token is created for the page's forms, and any message queued for
    the visitor is shown here and only here.
    """
    context.setdefault("user", auth.session_user(request))
    context.setdefault("operator", auth.is_operator_request(request))
    context.setdefault("flashes", auth.pop_flashes(request))
    context.setdefault("csrf_token", csrf.token(request))
    context.setdefault("active", "")
    return templates.TemplateResponse(request, template, context,
                                      status_code=status_code)
