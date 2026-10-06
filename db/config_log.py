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
