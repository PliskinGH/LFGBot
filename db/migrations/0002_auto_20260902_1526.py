from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0001_initial')]

    initial = False

    operations = [
        ops.AddField(
            model_name='GameParameter',
            name='display_name',
            field=fields.TextField(default='', db_default='', unique=False),
        ),
    ]
