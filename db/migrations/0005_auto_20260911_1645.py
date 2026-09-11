from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0004_auto_20260911_1414')]

    initial = False

    operations = [
        ops.CreateModel(
            name='RollCategory',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('guild', fields.ForeignKeyField('models.Guild', source_field='guild_id', db_constraint=True, to_field='guild_id', related_name='roll_categories', on_delete=OnDelete.CASCADE)),
                ('name', fields.TextField(unique=False)),
                ('items', fields.TextField(unique=False)),
            ],
            options={'table': 'roll_categories', 'app': 'models', 'unique_together': (('guild', 'name'),), 'pk_attr': 'id', 'table_description': 'One roll category of a guild: a row per rolls.ini key.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='RollItem',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('category', fields.ForeignKeyField('models.RollCategory', source_field='category_id', db_constraint=True, to_field='id', related_name='roll_items', on_delete=OnDelete.CASCADE)),
                ('name', fields.TextField(unique=False)),
            ],
            options={'table': 'roll_items', 'app': 'models', 'pk_attr': 'id', 'table_description': 'One rollable item of a category: a row per name in a rolls.ini set.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='RollDescription',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('item', fields.ForeignKeyField('models.RollItem', source_field='item_id', db_constraint=True, to_field='id', related_name='descriptions', on_delete=OnDelete.CASCADE)),
                ('description', fields.TextField(default='', db_default='', unique=False)),
                ('color', fields.IntField(null=True)),
                ('image_url', fields.TextField(null=True, unique=False)),
                ('thumbnail_url', fields.TextField(null=True, unique=False)),
            ],
            options={'table': 'roll_descriptions', 'app': 'models', 'pk_attr': 'id', 'table_description': 'One roll description embed of an item, from rolls_descriptions.json.'},
            bases=['Model'],
        ),
        ops.AlterModelOptions(
            name='Guild',
            options={'table': 'guilds', 'app': 'models', 'pk_attr': 'guild_id', 'table_description': "A Discord guild's configuration rows; sentinel guild id 0 = the [DEFAULT] config."},
        ),
    ]
