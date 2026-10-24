from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0005_user_deleted_at')]

    operations = [
        migrations.AddField(model_name='user', name='email_verification_required',
                            field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name='user', name='email_verified_at',
                            field=models.DateTimeField(null=True, blank=True, editable=False)),
    ]
