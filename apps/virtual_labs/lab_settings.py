"""Настройки модуля «Виртуальные лаборатории».

Модуль не требует правок в настройках основной платформы: всё, что можно поменять,
задаётся необязательным словарём VIRTUAL_LABS в settings.py, остальное берётся по умолчанию.
"""
import os

from django.conf import settings

DEFAULTS = {
    # Роли по логину или email (через запятую в переменных окружения)
    'ADMIN_LOGINS': os.environ.get('VIRTUAL_LABS_ADMIN_LOGINS', ''),
    'TUTOR_LOGINS': os.environ.get('VIRTUAL_LABS_TUTOR_LOGINS', ''),
    # Первый зарегистрированный пользователь пустой базы становится администратором (только автономный запуск)
    'FIRST_USER_ADMIN': False,
    # Может ли администратор модуля блокировать учётные записи. В AI edu этим занимается основная платформа.
    'MANAGE_ACCOUNTS': True,
    # Ограничение частоты запросов (формат DRF: число/second|minute|hour|day)
    'THROTTLE_RATES': {
        'ai': '30/minute',
        'tts': '20/minute',
        'literature': '30/minute',
        'upload': '20/hour',
        'export': '60/hour',
        'write': '240/minute',
        'lti': '60/minute',
        'auth': '20/minute',
    },
    # Файлы
    'MAX_UPLOAD_BYTES': 10 * 1024 * 1024,
    'MAX_UNPACKED_BYTES': 60 * 1024 * 1024,
    'UPLOAD_EXTENSIONS': ('.docx', '.pdf', '.pptx', '.txt', '.md'),
    'MAX_EXTRACTED_CHARS': 200_000,
    'UPLOAD_DIR': 'virtual_labs/uploads/%Y/%m',
    # Резервные копии
    'BACKUP_DIR': os.environ.get('VIRTUAL_LABS_BACKUP_DIR', ''),
    'BACKUP_KEEP': int(os.environ.get('VIRTUAL_LABS_BACKUP_KEEP', '14') or 14),
    # Внешние сервисы
    'AI_TIMEOUT_SECONDS': int(os.environ.get('VIRTUAL_LABS_AI_TIMEOUT', '120') or 120),
    'LITERATURE_TIMEOUT_SECONDS': 15,
    'CONTACT_EMAIL': os.environ.get('VIRTUAL_LABS_CONTACT_EMAIL', ''),
    # Работы, которые регистрируются как практики AI edu (№ 1–3 там уже есть)
    'PRACTICE_WORKS': ('vw04', 'vw05', 'vw06', 'vw07', 'vw08', 'vw09', 'vw10', 'vw11', 'vw12', 'vw13', 'vw14'),
    # LTI 1.3: адрес инструмента, который видит LMS (например, https://labs.example.kz)
    'LTI_TOOL_URL': os.environ.get('VIRTUAL_LABS_LTI_TOOL_URL', ''),
    # После LTI-входа открывать этот адрес интерфейса
    'LTI_FRONTEND_PATH': '/#/virtual-labs',
}


def conf(name):
    user = getattr(settings, 'VIRTUAL_LABS', {}) or {}
    if name == 'THROTTLE_RATES':
        return {**DEFAULTS['THROTTLE_RATES'], **user.get('THROTTLE_RATES', {})}
    return user.get(name, DEFAULTS[name])


def login_set(name):
    value = conf(name)
    if isinstance(value, str):
        value = value.split(',')
    return {str(x).strip().lower() for x in value if str(x).strip()}
