from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0006_auto_20260911_1800')]

    initial = False

    operations = [
        ops.CreateModel(
            name='ConfigChange',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('guild_id', fields.BigIntField(db_index=True)),
                ('actor_id', fields.BigIntField()),
                ('actor_name', fields.TextField(default='', db_default='', unique=False)),
                ('source', fields.TextField(unique=False)),
                ('action', fields.TextField(unique=False)),
                ('summary', fields.TextField(default='', db_default='', unique=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('applied_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ],
            options={'table': 'config_changes', 'app': 'models', 'pk_attr': 'id', 'table_description': 'One configuration write, from a Discord command or the web panel.'},
            bases=['Model'],
        ),
    ]
