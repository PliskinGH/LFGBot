from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    initial = True

    operations = [
        ops.CreateModel(
            name='DefaultApiField',
            fields=[
                ('key', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=255)),
                ('field_name', fields.TextField(unique=False)),
            ],
            options={'table': 'default_api_fields', 'app': 'models', 'pk_attr': 'key', 'table_description': 'The fixed match payload component field names from the [DEFAULT]'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='Guild',
            fields=[
                ('guild_id', fields.BigIntField(generated=True, primary_key=True, unique=True, db_index=True)),
            ],
            options={'table': 'guilds', 'app': 'models', 'pk_attr': 'guild_id', 'table_description': 'A guild and its games; sentinel guild id 0 = the [DEFAULT] config.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='Game',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('guild', fields.ForeignKeyField('models.Guild', source_field='guild_id', db_constraint=True, to_field='guild_id', related_name='games', on_delete=OnDelete.CASCADE)),
                ('command', fields.TextField(unique=False)),
                ('name', fields.TextField(default='', unique=False)),
                ('role', fields.TextField(default='', unique=False)),
                ('icon', fields.TextField(default='', unique=False)),
                ('color', fields.TextField(default='', unique=False)),
                ('forum', fields.TextField(null=True, unique=False)),
                ('tag', fields.TextField(null=True, unique=False)),
                ('visibility', fields.TextField(null=True, unique=False)),
                ('message', fields.TextField(null=True, unique=False)),
                ('registration_api', fields.TextField(null=True, unique=False)),
                ('match_api', fields.TextField(null=True, unique=False)),
                ('match_url', fields.TextField(null=True, unique=False)),
                ('api_token_env_var', fields.TextField(null=True, unique=False)),
                ('website_url', fields.TextField(null=True, unique=False)),
                ('registration_url', fields.TextField(null=True, unique=False)),
                ('profile_url', fields.TextField(null=True, unique=False)),
                ('default_max_guests', fields.IntField(null=True)),
            ],
            options={'table': 'games', 'app': 'models', 'unique_together': (('guild', 'command'),), 'pk_attr': 'id', 'table_description': 'One game configured for a guild: a row per ``GameOption``.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='GameApiFieldOverride',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('game', fields.ForeignKeyField('models.Game', source_field='game_id', db_constraint=True, to_field='id', related_name='api_field_overrides', on_delete=OnDelete.CASCADE)),
                ('key', fields.TextField(unique=False)),
                ('field_name', fields.TextField(unique=False)),
            ],
            options={'table': 'game_api_field_overrides', 'app': 'models', 'unique_together': (('game', 'key'),), 'pk_attr': 'id', 'table_description': "A game's override of a fixed match payload component (api_* key)."},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='GameParameter',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('game', fields.ForeignKeyField('models.Game', source_field='game_id', db_constraint=True, to_field='id', related_name='parameters', on_delete=OnDelete.CASCADE)),
                ('name', fields.TextField(unique=False)),
                ('api_field', fields.TextField(null=True, unique=False)),
            ],
            options={'table': 'game_parameters', 'app': 'models', 'unique_together': (('game', 'name'),), 'pk_attr': 'id', 'table_description': 'An optional slash-command parameter of a game.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='ParameterValue',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('parameter', fields.ForeignKeyField('models.GameParameter', source_field='parameter_id', db_constraint=True, to_field='id', related_name='values', on_delete=OnDelete.CASCADE)),
                ('value', fields.TextField(unique=False)),
                ('display_name', fields.TextField(unique=False)),
            ],
            options={'table': 'game_parameter_values', 'app': 'models', 'unique_together': (('parameter', 'value'),), 'pk_attr': 'id', 'table_description': 'One acceptable value of a game parameter, with its display name.'},
            bases=['Model'],
        ),
    ]
