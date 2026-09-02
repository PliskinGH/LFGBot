"""Store the games' API token VALUE instead of the env-var name, backfilling
existing rows by resolving the env vars.

The config files still name env vars (unchanged); the cog resolves them at
load time and stores the token itself, so server admins manage tokens via
/games without touching the bot's environment. The backfill keeps existing
deployments working: each row's api_token_env_var is looked up in os.environ
and the resolved value is written to api_token (blank when unset).

Not reversible: an env-var name cannot be recovered from a token value.
"""
import os

from tortoise import fields, migrations
from tortoise.migrations import operations as ops


async def resolve_tokens(apps, schema_editor):
    """Copy ``os.environ[api_token_env_var]`` into ``api_token`` for each row.

    Uses the historical model from the migration state (which still knows
    both columns at this point: after AddField, before RemoveField).
    """
    game = apps.get_model("models", "Game")
    rows = await game.exclude(api_token_env_var="").values(
        "id", "api_token_env_var")
    for row in rows:
        token = os.environ.get(row["api_token_env_var"]) or ""
        await game.filter(id=row["id"]).update(api_token=token)


class Migration(migrations.Migration):
    dependencies = [('models', '0002_auto_20260902_1526')]

    initial = False

    operations = [
        # 1) The new column, empty everywhere: db_default backfills the
        #    ADD COLUMN so populated tables do not fail.
        ops.AddField(
            model_name='Game',
            name='api_token',
            field=fields.TextField(default='', db_default='', unique=False),
        ),
        # 2) Resolve each row's env var into the token value.
        ops.RunPython(resolve_tokens, reverse_code=ops.RunPython.noop),
        # 3) Drop the old column: only the token value is stored from now on.
        ops.RemoveField(model_name='Game', name='api_token_env_var'),
    ]