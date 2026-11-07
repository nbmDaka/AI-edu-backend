from django.apps import AppConfig
from django.db.models.signals import post_migrate


class VirtualLabsConfig(AppConfig):
    name = 'apps.virtual_labs'
    label = 'virtual_labs'
    verbose_name = 'Виртуальные лаборатории'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from .catalog import sync_catalog
        from .practice import register_practices
        post_migrate.connect(sync_catalog, sender=self, dispatch_uid='virtual_labs_sync_catalog')
        register_practices()
