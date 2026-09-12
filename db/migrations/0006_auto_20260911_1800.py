from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0005_auto_20260911_1645')]

    initial = False

    operations = [
        ops.AddField(
            model_name='RollItem',
            name='active',
            field=fields.BooleanField(default=True, db_default=True, null=False),
        ),
        ops.RemoveField(
            model_name='RollCategory',
            name='items',
        ),
        ops.AlterModelOptions(
            name='RollCategory',
            options={'table': 'roll_categories', 'app': 'models', 'unique_together': (('guild', 'name'),), 'pk_attr': 'id', 'table_description': 'One roll category of a guild; its set is given by its active items.'},
        ),
        ops.AlterModelOptions(
            name='RollItem',
            options={'table': 'roll_items', 'app': 'models', 'pk_attr': 'id', 'table_description': 'One rollable item of a category: a row per name ever configured.'},
        ),
    ]