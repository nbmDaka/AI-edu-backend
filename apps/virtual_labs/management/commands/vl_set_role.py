"""Назначить роль в модуле: python manage.py vl_set_role <логин или email> teacher|tutor|admin"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ...models import LabRole


class Command(BaseCommand):
    help = 'Назначить пользователю роль модуля «Виртуальные лаборатории».'

    def add_arguments(self, parser):
        parser.add_argument('login')
        parser.add_argument('role', choices=['teacher', 'tutor', 'admin'])

    def handle(self, login, role, **options):
        User = get_user_model()
        user = User.objects.filter(**{f'{User.USERNAME_FIELD}__iexact': login}).first()
        if not user:
            raise CommandError('Пользователь не найден')
        LabRole.objects.update_or_create(user=user, defaults={'role': role})
        self.stdout.write(self.style.SUCCESS(f'{user.get_username()}: {role}'))
