"""Filling in the templates: the one place that knows what every page carries."""

from pathlib import Path

from jinja2 import StrictUndefined
from starlette.exceptions import HTTPException
from starlette.templating import Jinja2Templates

from db import config_log

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


async def submitted(request):
    """The submitted form, refusing one that another site sent."""
    form = await request.form()
    if (not csrf.is_valid(request, form.get(csrf.FORM_FIELD))):
        raise HTTPException(403, "The form was not sent by this session: "
                                 "try again.")
    return form


def submitted_values(form) -> dict:
    """What a form posted, for showing it again after an error."""
    return {key: value for key, value in form.items() if isinstance(value, str)}


async def log_change(request, guild_id: int, action: str,
                     summary: str = "") -> None:
    """Log a panel write as the queued change the bot will pick up."""
    user = auth.session_user(request)
    await config_log.record_change(
        guild_id, actor_id=(user or {}).get("id") or 0,
        actor_name=(user or {}).get("name") or "",
        source=config_log.SOURCE_WEB, action=action, summary=summary)
