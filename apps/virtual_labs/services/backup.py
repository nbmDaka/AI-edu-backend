"""Резервное копирование базы (ТЗ, п. 38).

SQLite — копия файла базы средствами sqlite3 (безопасно при работающем сервере).
PostgreSQL — pg_dump, если он установлен; иначе выгрузка таблиц модуля в JSON (dumpdata).
Старые копии сверх VIRTUAL_LABS['BACKUP_KEEP'] удаляются. По расписанию копия создаётся
командой `python manage.py vl_backup` (cron, планировщик Windows или сервис backup в docker-compose).
"""
import gzip
import io
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from ..lab_settings import conf


def backup_dir():
    path = Path(conf('BACKUP_DIR') or Path(settings.BASE_DIR) / 'backups')
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sqlite_file():
    name = str(connection.settings_dict.get('NAME') or '')
    return name if name and ':memory:' not in name and 'mode=memory' not in name and Path(name).exists() else None


def _sqlite(target):
    src = sqlite3.connect(_sqlite_file(), timeout=30)
    dst = sqlite3.connect(str(target))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def _pg_dump(target):
    db = connection.settings_dict
    exe = shutil.which('pg_dump')
    if not exe:
        raise FileNotFoundError('pg_dump не найден')
    env = {**os.environ, 'PGPASSWORD': str(db.get('PASSWORD') or '')}
    cmd = [exe, '--format=custom', '--no-owner', '-f', str(target), '-h', str(db.get('HOST') or 'localhost'),
           '-p', str(db.get('PORT') or 5432), '-U', str(db.get('USER') or ''), str(db['NAME'])]
    subprocess.run(cmd, check=True, env=env, capture_output=True, timeout=1800)


def _json(target):
    buf = io.StringIO()
    call_command('dumpdata', 'virtual_labs', stdout=buf, indent=None)
    with gzip.open(target, 'wt', encoding='utf-8') as fh:
        fh.write(buf.getvalue())


def create_backup():
    """Создаёт резервную копию и запись в журнале. Возвращает BackupRecord."""
    from ..models import BackupRecord
    stamp = timezone.now().strftime('%Y%m%d-%H%M%S-%f')
    folder = backup_dir()
    vendor = connection.vendor
    try:
        if vendor == 'sqlite' and _sqlite_file():
            target, method = folder / f'aiedu-{stamp}.sqlite3', 'sqlite'
            _sqlite(target)
        elif vendor == 'postgresql' and shutil.which('pg_dump'):
            target, method = folder / f'aiedu-{stamp}.dump', 'pg_dump'
            _pg_dump(target)
        else:
            target, method = folder / f'virtual-labs-{stamp}.json.gz', 'json'
            _json(target)
        record = BackupRecord.objects.create(path=str(target), method=method, size=target.stat().st_size)
    except Exception as exc:  # журналируем любую ошибку копирования
        record = BackupRecord.objects.create(path=str(folder), method=vendor, ok=False, error=str(exc)[:2000])
    prune(folder)
    return record


def prune(folder):
    keep = max(1, int(conf('BACKUP_KEEP')))
    files = sorted([p for p in folder.iterdir() if p.is_file() and p.name.startswith(('aiedu-', 'virtual-labs-'))],
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)
