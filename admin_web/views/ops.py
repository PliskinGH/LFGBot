"""The operator pages: every server, the [DEFAULT] configuration, and the log."""

from datetime import timedelta

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse
from tortoise import timezone

from db import config_log, models

from .. import auth, csrf, discord_reads, overview, pages

# The sentinel guild id the cogs keep the [DEFAULT] configuration under.
DEFAULT_GUILD_ID = 0
PAGE_SIZE = 50
PRUNE_DAYS = 90
# A wait longer than this means the bot is not applying what the panel writes.
STALE_AFTER_MINUTES = 5
# Why a configured server has no name to show, in the two cases there are.
NOT_IN_SERVER = "the bot is not in this server"
UNREADABLE = "Discord could not be read"
# The state filter of the log, as ``(value, label)``.
STATES = (("", "Any state"), ("pending", "Waiting for the bot"),
          ("applied", "Applied"))


@auth.require_operator
async def hub(request):
    """Every server, with what the panel knows about each of them."""
    reads = request.app.state.discord
    stored = {guild.guild_id for guild in await models.Guild.all()}
    readable = reads.can_read()
    rows: dict[int, dict] = {}
    try:
        for guild in await reads.guilds():
            rows[guild["id"]] = {"id": guild["id"], "name": guild["name"],
                                 "known": True}
    except discord_reads.UNREACHABLE:
        readable = False
        auth.flash(request, "warning", "Discord could not be reached: only "
                                       "the configured servers are listed.")
    for guild_id in stored:
        if (guild_id != DEFAULT_GUILD_ID):
            # A configured server Discord cannot name still belongs here; its
            # id already has a column of its own, so it is marked instead.
            rows.setdefault(guild_id, {"id": guild_id, "name": "", "known": False,
                                       "note": NOT_IN_SERVER if readable
                                               else UNREADABLE})
    for row in rows.values():
        row["configured"] = row["id"] in stored
    return pages.render(request, "ops.html", active="ops",
                        guilds=sorted(rows.values(), key=_order),
                        knows_default=DEFAULT_GUILD_ID in stored,
                        pending=await config_log.pending_count(),
                        waiting=await _waiting())


def _order(row: dict):
    """Named servers first, by name; the unnamed ones after them, by id."""
    return (0, row["name"].lower()) if row["known"] else (1, str(row["id"]))


@auth.require_operator
async def default_page(request):
    """The [DEFAULT] configuration every unconfigured server inherits."""
    return pages.render(request, "guild.html", active="ops_default",
                        guild_id=DEFAULT_GUILD_ID, guild_name="[DEFAULT]",
                        is_default=True,
                        **await overview.guild_config(
                            request.app.state.discord, DEFAULT_GUILD_ID))


@auth.require_operator
async def changes_page(request):
    """The change log, filterable, newest first."""
    filters = _filters(request)
    changes, total = await config_log.change_page(
        guild_id=filters["guild_id"], source=filters["source"],
        pending=filters["pending"], limit=PAGE_SIZE, offset=filters["offset"])
    return pages.render(request, "changes.html", active="ops_changes",
                        changes=changes, total=total, filters=filters,
                        size=PAGE_SIZE, states=STATES,
                        sources=(config_log.SOURCE_DISCORD, config_log.SOURCE_WEB),
                        pending=await config_log.pending_count(),
                        waiting=await _waiting())


@auth.require_operator
async def prune_page(request):
    """Delete the applied log rows older than a number of days; GET confirms."""
    if (request.method == "POST"):
        form = await request.form()
        if (not csrf.is_valid(request, form.get(csrf.FORM_FIELD))):
            raise HTTPException(403,
                                "The form was not sent by this session: try again.")
        days = _days(form.get("days"))
        if (days is None):
            auth.flash(request, "error", "Give a number of days of at least 1.")
            return RedirectResponse(request.url_for("ops_prune"), status_code=303)
        deleted = await config_log.prune_applied(_cutoff(days))
        auth.flash(request, "success", f"Deleted {deleted} applied change(s) "
                                       f"older than {days} day(s).")
        return RedirectResponse(request.url_for("ops_changes"), status_code=303)
    return pages.render(request, "prune.html", active="ops_changes",
                        days=PRUNE_DAYS,
                        prunable=await config_log.applied_before(_cutoff(PRUNE_DAYS)),
                        pending=await config_log.pending_count())


async def _waiting() -> dict | None:
    """How long the oldest waiting change has waited, when it is worth saying.

    A change applied within the minute is the normal case, so nothing is shown;
    a longer wait is reported, and flagged once the bot should have caught up.
    """
    minutes = await config_log.pending_age_minutes()
    if (minutes is None or minutes < 1):
        return None
    return {"minutes": minutes, "stale": minutes >= STALE_AFTER_MINUTES}


def _filters(request) -> dict:
    """The log filters a page asked for, read from its query string."""
    query = request.query_params
    source = query.get("source")
    state = query.get("state") or ""
    page = max(_int(query.get("page")) or 1, 1)
    return {
        "guild_id": _int(query.get("guild_id")),
        "source": source if source in (config_log.SOURCE_DISCORD,
                                       config_log.SOURCE_WEB) else None,
        "state": state,
        "pending": {"pending": True, "applied": False}.get(state),
        "page": page,
        "offset": (page - 1) * PAGE_SIZE,
    }


def _cutoff(days: int):
    """The moment a change has to be older than to be pruned."""
    return timezone.now() - timedelta(days=days)


def _days(value) -> int | None:
    """A number of days a form asked for, or None when it is not a usable one."""
    days = _int(value)
    return days if days and days >= 1 else None


def _int(value) -> int | None:
    """A query or form value read as a whole number, or None."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
