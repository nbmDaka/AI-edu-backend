"""Резервная копия базы: python manage.py vl_backup [--every-hours 24]

Без параметров создаёт одну копию (для cron или планировщика Windows).
С --every-hours работает постоянно и создаёт копию по расписанию (сервис backup в docker-compose)."""
import time

from django.core.management.base import BaseCommand

from ...services.backup import create_backup


class Command(BaseCommand):
    help = 'Создать резервную копию базы данных (однократно или по расписанию).'

    def add_arguments(self, parser):
        parser.add_argument('--every-hours', type=float, default=0, help='Повторять каждые N часов')

    def handle(self, *args, every_hours=0, **options):
        while True:
            record = create_backup()
            if record.ok:
                self.stdout.write(self.style.SUCCESS(f'Резервная копия: {record.path} ({record.size} байт)'))
            else:
                self.stderr.write(f'Ошибка резервного копирования: {record.error}')
            if not every_hours:
                return
            time.sleep(every_hours * 3600)
