from django.apps import AppConfig


class EducationConfig(AppConfig):
    name = 'apps.education'
    label = 'education'

    def ready(self):
        from . import activity  # noqa: F401  (connects the login receiver of the activity log)
