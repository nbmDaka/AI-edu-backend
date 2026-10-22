from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mediafiles', '0003_mediafile_deleted_at')]

    operations = [
        migrations.AlterField(
            model_name='mediafile', name='content_type',
            field=models.CharField(max_length=255),
        ),
    ]
