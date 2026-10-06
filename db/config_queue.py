"""The queue of configuration writes made outside the bot's own process.

A write from the web panel cannot touch the running bot's memory: it is logged
as a pending ``ConfigChange`` and applied here, on the tick of the bot's
watcher (``bot.py``), which reloads the cog owning the action and stamps the
rows it applied.
"""

from . import config_log


def _owners_by_prefix(bot) -> dict[str, object]:
    """The cogs that own queued changes, by the action prefix each of them set."""
    owners: dict[str, object] = {}
    for cog in (getattr(bot, "cogs", None) or {}).values():
        if (callable(getattr(cog, "reload_config", None))):
            for prefix in getattr(cog, "change_prefixes", ()):
                owners[prefix] = cog
    return owners


def _owner_of(action: str, owners: dict[str, object]):
    """The cog owning a change action, or None when no loaded cog claims it."""
    for prefix, cog in owners.items():
        if (action.startswith(prefix)):
            return cog
    return None


async def apply_pending(bot) -> int:
    """Apply every change the bot owes; how many rows were stamped.

    One reload per cog rather than per change: a cog reloads its whole
    configuration from the database in one go. A cog whose reload fails keeps
    its rows pending, so the next tick retries them, and an action no loaded
    cog owns is reported and left pending rather than applied unseen.
    """
    if (getattr(bot, "db", None) is None):
        return 0
    try:
        pending = await config_log.pending_changes()
    except Exception as error:
        print(f"Web panel: could not read the waiting changes ({error}).")
        return 0
    owners = _owners_by_prefix(bot)
    owned: dict[object, list] = {}
    unowned: set[str] = set()
    for changes in pending.values():
        for change in changes:
            owner = _owner_of(change.action, owners)
            if (owner is None):
                unowned.add(change.action)
            else:
                owned.setdefault(owner, []).append(change)
    for action in sorted(unowned):
        print(f"Web panel: no loaded cog owns a '{action}' change; "
              "leaving it queued.")
    applied = 0
    for owner, changes in sorted(owned.items(),
                                 key=lambda item: type(item[0]).__name__):
        if (not await _apply(owner, changes)):
            continue
        applied += len(changes)
    return applied


async def _apply(owner, changes: list) -> bool:
    """Reload one cog and stamp its changes; False when the reload failed."""
    try:
        await owner.reload_config()
    except Exception as error:
        print(f"Web panel: could not reload the configuration "
              f"({type(owner).__name__}): {error}; its change(s) stay queued.")
        return False
    syncer = getattr(owner, "sync_applied", None)
    if (callable(syncer)):
        guild_ids = {change.guild_id for change in changes}
        await _sync_commands(owner, syncer, guild_ids)
    await config_log.mark_applied(changes)
    return True


async def _sync_commands(owner, syncer, guild_ids: set[int]) -> None:
    """Re-sync the guilds' commands; the configuration itself is already applied."""
    try:
        await syncer(guild_ids)
    except Exception as error:
        print(f"Web panel: could not re-sync the commands "
              f"({type(owner).__name__}): {error}; the next restart re-syncs.")
