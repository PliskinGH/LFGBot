"""The roll-set pages: a server's categories, their items and their variants.

Every write goes through the matchrolls cog's own ``db_config`` writers and logs
one ``ConfigChange`` the way the matching ``/rollsets`` command does, so the
bot's watcher applies it and the log reads the same whichever surface made the
change. A category and an item are addressed by their row id: a name is not
unique across a server's categories.
"""

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse
from starlette.routing import Route

from cogs.matchrolls import constants as rolls_constants
from cogs.matchrolls import db_config as rolls_db_config

from .. import auth, forms, overview, pages
from . import sections

DEFAULT_GUILD_ID = rolls_constants.DEFAULT_GUILD_ID
NO_SUCH_CATEGORY = "This server has no such roll category."
NO_SUCH_ITEM = "This roll category has no such item."
NO_SUCH_VARIANT = "This item has no such description variant."
DEFAULT_OWNS_EVERYTHING = ("The [DEFAULT] configuration already owns every "
                           "roll category: there is nothing to copy.")


def routes() -> list[Route]:
    """The roll-set pages of one server, and the same ones for [DEFAULT]."""
    return sections.routes(PAGES)


async def new_category(request, guild_id: int):
    """Add a roll category to a server (or to [DEFAULT])."""
    if (request.method == "GET"):
        return _page(request, guild_id, "rollset_form.html", new=True,
                     values={"name": "", "items_text": ""}, items=[])
    form = await pages.submitted(request)
    arguments, errors = forms.category_form(form)
    if (not errors and not await rolls_db_config.add_category(
            guild_id, arguments["name"], arguments["item_names"])):
        errors.append(f"`{arguments['name']}` is already a roll category here.")
    if (errors):
        return _page(request, guild_id, "rollset_form.html", new=True,
                     values=_category_values(form), items=[], errors=errors)
    await _log(request, guild_id, "rollset.category.add", arguments["name"])
    auth.flash(request, "success",
               f"Roll category `{arguments['name']}` added.")
    return _back(request, guild_id)


async def edit_category(request, guild_id: int):
    """Edit a category: rename it and replace its item set, in one form."""
    detail = await _detail(request, guild_id)
    current = detail["category"].name
    if (request.method == "GET"):
        return _page(request, guild_id, "rollset_form.html", new=False,
                     **detail)
    form = await pages.submitted(request)
    arguments, errors = forms.category_form(form)
    name = arguments.get("name", current)
    if (not errors and name != current):
        sets = await rolls_db_config.effective_category_sets(guild_id)
        if (name in sets):
            errors.append(f"`{name}` is already a roll category here.")
    if (not errors and not await rolls_db_config.update_category(
            guild_id, current, item_names=arguments["item_names"],
            new_name=name if (name != current) else None)):
        errors.append(f"`{current}` is not a roll category of this server "
                      "any more.")
    if (errors):
        return _page(request, guild_id, "rollset_form.html", new=False,
                     category=detail["category"], items=detail["items"],
                     values=_category_values(form), errors=errors)
    summary = f"{current} -> {name}" if (name != current) else current
    await _log(request, guild_id, "rollset.category.update", summary)
    auth.flash(request, "success",
               f"Roll category `{current}` updated: {summary}.")
    return _back(request, guild_id)

async def remove_category(request, guild_id: int):
    """Delete a roll category: a confirmation page in, a deletion out."""
    detail = await _detail(request, guild_id)
    category_id = detail["category"].id
    name = detail["category"].name
    if (request.method == "GET"):
        return _page(request, guild_id, "confirm.html",
                     title=f"Remove {name}",
                     message=(f"Roll category `{name}`, its items and every "
                              "description variant they have are removed from "
                              "this configuration. This cannot be undone."),
                     action_url=(f"{_base(request, guild_id)}/rollsets/"
                                 f"{category_id}/remove"),
                     cancel_url=_base(request, guild_id))
    await pages.submitted(request)
    if (not await rolls_db_config.delete_category(guild_id, name)):
        raise HTTPException(404, NO_SUCH_CATEGORY)
    await _log(request, guild_id, "rollset.category.remove", name)
    auth.flash(request, "success", f"Roll category `{name}` removed.")
    return _back(request, guild_id)


async def adopt_category(request, guild_id: int):
    """Give a server its own copy of a roll category it inherits, then open it.

    Materializing copies the whole [DEFAULT] configuration; a category [DEFAULT]
    gained after the server already had a configuration is copied on its own.
    """
    if (guild_id == DEFAULT_GUILD_ID):
        raise HTTPException(404, DEFAULT_OWNS_EVERYTHING)
    form = await pages.submitted(request)
    posted = form.get("name")
    name = posted if isinstance(posted, str) else ""
    category_id = await _materialize_category(guild_id, name)
    if (category_id is None):
        raise HTTPException(404, NO_SUCH_CATEGORY)
    await _log(request, guild_id, "rollset.category.adopt", name)
    auth.flash(request, "success",
               f"`{name}` is now part of this server's own configuration.")
    return RedirectResponse(
        f"{_base(request, guild_id)}/rollsets/{category_id}", status_code=303)


async def _materialize_category(guild_id: int, name: str) -> int | None:
    """The server's own row for a category, copying [DEFAULT]'s in when needed.

    The name is checked against the configuration the server rolls before
    anything is copied, so a name nobody has cannot materialize its rows.
    """
    sets = await rolls_db_config.effective_category_sets(guild_id)
    if (name not in sets):
        return None
    await rolls_db_config.ensure_guild_categories(guild_id)
    own = {category.name: category
           for category in await rolls_db_config.own_categories(guild_id)}
    if (name in own):
        return own[name].id
    if (not await rolls_db_config.adopt_category(guild_id, name)):
        return None
    own = {category.name: category
           for category in await rolls_db_config.own_categories(guild_id)}
    category = own.get(name)
    return None if (category is None) else category.id


async def item_variants(request, guild_id: int):
    """One item's description variants, and the button that adds another."""
    detail = await _item_detail(request, guild_id)
    return _page(request, guild_id, "item_variants.html", **detail)


async def new_variant(request, guild_id: int):
    """Add a description variant to one of the server's items."""
    detail = await _item_detail(request, guild_id)
    label = rolls_db_config.item_label(detail["item"])
    if (request.method == "GET"):
        return _page(request, guild_id, "variant_form.html", new=True,
                     values=_blank_variant(), **detail)
    form = await pages.submitted(request)
    fields, errors = forms.variant_form(form)
    if (not errors and not await rolls_db_config.add_description(
            guild_id, detail["item"].id, fields)):
        errors.append(f"There is no active item `{label}` in this server.")
    if (errors):
        return _page(request, guild_id, "variant_form.html", new=True,
                     values=_variant_input(form), errors=errors, **detail)
    await _log(request, guild_id, "rollset.description.add", label)
    auth.flash(request, "success",
               f"Description variant added to `{label}`.")
    return _back_to_item(request, guild_id, detail)


async def edit_variant(request, guild_id: int):
    """Change one description variant of one of the server's items."""
    detail = await _item_detail(request, guild_id)
    variant = _variant(detail, int(request.path_params["variant_id"]))
    label = rolls_db_config.item_label(detail["item"])
    if (request.method == "GET"):
        return _page(request, guild_id, "variant_form.html", new=False,
                     values=_variant_values(variant), variant_id=variant["id"],
                     **detail)
    form = await pages.submitted(request)
    fields, errors = forms.variant_form(form)
    if (not errors and not await rolls_db_config.update_description(
            guild_id, detail["item"].id, variant["index"], fields)):
        errors.append("This description variant is not here any more.")
    if (errors):
        return _page(request, guild_id, "variant_form.html", new=False,
                     values=_variant_input(form), errors=errors,
                     variant_id=variant["id"], **detail)
    await _log(request, guild_id, "rollset.description.update",
               f"{label} #{variant['index']}")
    auth.flash(request, "success", f"Variant #{variant['index']} updated.")
    return _back_to_item(request, guild_id, detail)


async def remove_variant(request, guild_id: int):
    """Delete one description variant: a confirmation page in, a deletion out."""
    detail = await _item_detail(request, guild_id)
    variant = _variant(detail, int(request.path_params["variant_id"]))
    label = rolls_db_config.item_label(detail["item"])
    index = variant["index"]
    if (request.method == "GET"):
        return _page(request, guild_id, "confirm.html",
                     title=f"Remove variant #{index}",
                     message=(f"Description variant #{index} of `{label}` is "
                              "removed. This cannot be undone."),
                     action_url=(f"{_item_base(request, guild_id, detail)}"
                                 f"/variants/{variant['id']}/remove"),
                     cancel_url=_item_base(request, guild_id, detail))
    await pages.submitted(request)
    if (not await rolls_db_config.delete_description(
            guild_id, detail["item"].id, index)):
        raise HTTPException(404, NO_SUCH_VARIANT)
    await _log(request, guild_id, "rollset.description.remove",
               f"{label} #{index}")
    auth.flash(request, "success", f"Variant #{index} removed from `{label}`.")
    return _back_to_item(request, guild_id, detail)


def _category_values(form) -> dict:
    """What a category form posted, keyed the way its template reads it."""
    return {"name": _posted(form, "name"), "items_text": _posted(form, "items")}


def _posted(form, field: str) -> str:
    """One text field a form posted, blank when it posted none."""
    value = form.get(field)
    return value.strip() if isinstance(value, str) else ""


def _variant(detail: dict, variant_id: int) -> dict:
    """The variant row a page edits, or a 404 when the item has none.

    A variant is addressed by its row id and written by its 1-based position:
    the id says which row, the index is what the cog's writer takes.
    """
    for variant in detail["variants"]:
        if (variant["id"] == variant_id):
            return variant
    raise HTTPException(404, NO_SUCH_VARIANT)


def _blank_variant() -> dict:
    """An empty variant form: no text, no colour, no images."""
    return {"description": "", "color": forms.NO_COLOR, "color_set": False,
            "image_url": "", "thumbnail_url": ""}


def _variant_input(form) -> dict:
    """What a variant form posted, keyed the way its template reads it.

    An unusable colour falls back to the picker's default: the error is shown
    beside it, and the box still records whether a colour was meant.
    """
    posted = _posted(form, "color")
    return {"description": _posted(form, "description"),
            "color": posted if forms.color_stored(posted) else forms.NO_COLOR,
            "color_set": bool(form.get("use_color")),
            "image_url": _posted(form, "image_url"),
            "thumbnail_url": _posted(form, "thumbnail_url")}


def _variant_values(variant: dict) -> dict:
    """A stored variant's values, as the form reads them."""
    return {"description": variant["description"],
            "color": variant["color_hex"],
            "color_set": variant["color_set"],
            "image_url": variant["image_url"],
            "thumbnail_url": variant["thumbnail_url"]}


async def _detail(request, guild_id: int) -> dict:
    """One category's page detail, or a 404 when the server has no such row."""
    detail = await overview.rollset_detail(
        guild_id, int(request.path_params["category_id"]))
    if (detail is None):
        raise HTTPException(404, NO_SUCH_CATEGORY)
    return detail


async def _item_detail(request, guild_id: int) -> dict:
    """One item's page detail, or a 404 when the category has no such item."""
    detail = await overview.rollset_item_detail(
        guild_id, int(request.path_params["category_id"]),
        int(request.path_params["item_id"]))
    if (detail is None):
        raise HTTPException(404, NO_SUCH_ITEM)
    return detail


def _page(request, guild_id: int, template: str, **context):
    """Render a roll-set page with the values every page of the section carries."""
    context.setdefault("errors", [])
    return pages.render(request, template, active=_active(guild_id),
                        rollset_base=_base(request, guild_id),
                        guild_id=guild_id, **context)


def _active(guild_id: int) -> str:
    """The nav item a roll-set page belongs to."""
    return "ops_default" if (guild_id == DEFAULT_GUILD_ID) else "guild"


def _base(request, guild_id: int) -> str:
    """The URL a roll-set page hangs its form actions and its links off."""
    if (guild_id == DEFAULT_GUILD_ID):
        return str(request.url_for("ops_default"))
    return str(request.url_for("guild", guild_id=guild_id))


def _item_base(request, guild_id: int, detail: dict) -> str:
    """The URL of the item page a variant's pages hang off."""
    return (f"{_base(request, guild_id)}/rollsets/{detail['category'].id}"
            f"/items/{detail['item'].id}")


def _back(request, guild_id: int) -> RedirectResponse:
    """Send the manager back to the page of the server they edited."""
    return RedirectResponse(_base(request, guild_id), status_code=303)


def _back_to_item(request, guild_id: int, detail: dict) -> RedirectResponse:
    """Send the manager back to the item's page."""
    return RedirectResponse(_item_base(request, guild_id, detail),
                            status_code=303)


async def _log(request, guild_id: int, action: str, summary: str = "") -> None:
    """Log a panel write as the queued change the bot will pick up."""
    await pages.log_change(request, guild_id, action, summary)


# The roll-set pages, as ``(path, route name, view, methods)``. Each one is
# served for a single server and, under ``/ops/default``, for [DEFAULT].
# A category, an item and a variant are named by their row id: a name is not
# unique, and a category literally called "new" would be the add page.
PAGES = (
    ("rollsets/new", "rollset_new", new_category, ["GET", "POST"]),
    ("rollsets/adopt", "rollset_adopt", adopt_category, ["POST"]),
    ("rollsets/{category_id:int}", "rollset_edit", edit_category,
     ["GET", "POST"]),
    ("rollsets/{category_id:int}/remove", "rollset_remove", remove_category,
     ["GET", "POST"]),
    ("rollsets/{category_id:int}/items/{item_id:int}", "rollset_item",
     item_variants, ["GET"]),
    ("rollsets/{category_id:int}/items/{item_id:int}/variants/new",
     "variant_new", new_variant, ["GET", "POST"]),
    ("rollsets/{category_id:int}/items/{item_id:int}/variants/"
     "{variant_id:int}", "variant_edit", edit_variant, ["GET", "POST"]),
    ("rollsets/{category_id:int}/items/{item_id:int}/variants/"
     "{variant_id:int}/remove", "variant_remove", remove_variant,
     ["GET", "POST"]),
)

