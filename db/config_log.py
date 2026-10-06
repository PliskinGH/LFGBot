"""The configuration change log: what changed, by whom, and what the bot owes."""

from tortoise import timezone

from . import models

# The two writers of the log. A web write is pending until the bot applies it;
# a slash command applies its own write in-process before logging it.
SOURCE_DISCORD = "discord"
SOURCE_WEB = "web"


async def record_change(guild_id: int, *, actor_id: int, actor_name: str,
                        source: str, action: str, summary: str = "",
                        applied: bool = False) -> models.ConfigChange | None:
    """Log one configuration write, marking it applied when the bot already is.

    A log that cannot be written is reported and dropped: the write it follows
    has already happened, so failing it would lose the change, not the log.
    """
    try:
        return await models.ConfigChange.create(
            guild_id=guild_id, actor_id=actor_id, actor_name=actor_name,
            source=source, action=action, summary=summary,
            applied_at=timezone.now() if applied else None)
    except Exception as error:
        print(f"Could not log the configuration change ({action}): {error}")
        return None


async def record_command_change(interaction, action: str,
                                summary: str = "") -> models.ConfigChange | None:
    """Log a slash-command write, which its own cog has already applied.

    ``interaction`` is read for its guild and user only, so a test double
    works as well as a ``discord.Interaction``.
    """
    return await record_change(
        interaction.guild_id, actor_id=interaction.user.id,
        actor_name=getattr(interaction.user, "name", "") or "",
        source=SOURCE_DISCORD, action=action, summary=summary, applied=True)


async def pending_changes() -> dict[int, list[models.ConfigChange]]:
    """The changes the bot has to apply, grouped by the guild they belong to."""
    changes: dict[int, list[models.ConfigChange]] = {}
    for change in (await models.ConfigChange
                   .filter(applied_at=None).order_by("id")):
        changes.setdefault(change.guild_id, []).append(change)
    return changes


async def mark_applied(changes: list[models.ConfigChange]) -> None:
    """Stamp the changes the bot just applied."""
    if (not changes):
        return
    await models.ConfigChange.filter(
        id__in=[change.id for change in changes]).update(
        applied_at=timezone.now())


async def pending_count() -> int:
    """How many changes the bot has not applied yet."""
    return await models.ConfigChange.filter(applied_at=None).count()


async def pending_age_minutes() -> int | None:
    """How long the oldest change the bot still owes has been waiting, else None."""
    oldest = await (models.ConfigChange.filter(applied_at=None)
                    .order_by("id").first())
    if (oldest is None):
        return None
    return int((timezone.now() - oldest.created_at).total_seconds() // 60)


async def changes_for_guild(guild_id: int,
                            limit: int = 10) -> list[models.ConfigChange]:
    """The most recent changes of one guild, newest first."""
    return await (models.ConfigChange.filter(guild_id=guild_id)
                  .order_by("-id").limit(limit))


async def change_page(*, guild_id: int | None = None, source: str | None = None,
                      pending: bool | None = None,
                      limit: int = 50, offset: int = 0,
                      ) -> tuple[list[models.ConfigChange], int]:
    """A page of the log, newest first, with how many rows the filter matched."""
    query = models.ConfigChange.all()
    if (guild_id is not None):
        query = query.filter(guild_id=guild_id)
    if (source):
        query = query.filter(source=source)
    if (pending is not None):
        query = (query.filter(applied_at=None) if pending
                 else query.exclude(applied_at=None))
    total = await query.count()
    return (await query.order_by("-id").offset(offset).limit(limit), total)


async def applied_before(cutoff) -> int:
    """How many applied changes are older than a cutoff."""
    return await models.ConfigChange.filter(
        applied_at__not_isnull=True, applied_at__lt=cutoff).count()


async def prune_applied(cutoff) -> int:
    """Delete the applied changes older than a cutoff; how many went.

    Pending changes are never deleted: they are the bot's work queue, and a
    row left pending is the sign the bot has not caught up.
    """
    return await models.ConfigChange.filter(
        applied_at__not_isnull=True, applied_at__lt=cutoff).delete()
