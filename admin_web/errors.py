"""The error pages, rendered the way the panel renders its pages."""

from . import pages

TITLES = {403: "Not allowed", 404: "Not found", 405: "Not allowed",
          503: "Unavailable"}
BROKEN = ("Something went wrong on the panel's side. The bot itself is "
          "unaffected.")


async def http_error(request, error):
    """Render an HTTP error with the panel's layout."""
    status = getattr(error, "status_code", 500) or 500
    detail = getattr(error, "detail", None) or BROKEN
    context = {"status": status, "detail": detail,
               "title": TITLES.get(status, "Something went wrong")}
    if ("session" not in request.scope):
        # The request failed before the session middleware: nothing to read, so
        # the layout's own values are given explicitly.
        return pages.templates.TemplateResponse(
            request, "error.html",
            dict(context, user=None, operator=False, flashes=[], csrf_token="",
                 active=""),
            status_code=status)
    return pages.render(request, "error.html", status_code=status, **context)
