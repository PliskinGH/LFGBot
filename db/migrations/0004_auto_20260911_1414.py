from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0003_auto_20260902_1615')]

    initial = False

    operations = [
        ops.AddField(
            model_name='Game',
            name='channel',
            field=fields.TextField(null=True, unique=False),
        ),
    ]
