"""Практика «Виртуальная лаборатория «Биометрия»»: студент работает во внешней лаборатории (отдельный сервис), затем проходит самопроверку по критериям.

Лаборатория открывается во встроенном окне по адресу `lab_url` из настроек задания. Результат практики - самооценка по критериям
(как у остальных практик): сервер платформы не получает данных из лаборатории и не доверяет им."""
from dataclasses import replace
from ipaddress import ip_address
from urllib.parse import urlsplit

from rest_framework.exceptions import ValidationError

from .assignment import make_assignment_plugin

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
LANGUAGES = ('ru', 'kk', 'en')


def valid_lab_url(value):
    """HTTPS-адрес лаборатории без логина и пароля; http допускается только для локального адреса разработки."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2000:
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
    except ValueError:
        return False
    if parsed.username or parsed.password or not host or parsed.fragment:
        return False
    if parsed.scheme == 'https':
        return True
    if parsed.scheme == 'http' and (host in LOCAL_HOSTS):
        return True
    try:  # http допускается и для петлевых адресов вида 127.x.x.x
        return parsed.scheme == 'http' and ip_address(host).is_loopback
    except ValueError:
        return False


def make_biometric_lab_plugin(checklist_class):
    assignment = make_assignment_plugin(checklist_class)

    class BiometricLabPlugin(type(assignment)):
        # pylint: disable=too-many-ancestors
        manifest = {
            **assignment.manifest,
            'type': 'biometric_lab', 'version': '1.0.0',
            'name': 'Виртуальная лаборатория «Биометрия»',
            'description': 'Работа в лаборатории распознавания отпечатков, лица, радужки и голоса и самопроверка по критериям',
            'icon': 'fingerprint', 'frontend_editor': 'biometric_lab', 'student_runtime': 'biometric_lab',
            'capabilities': ['instructions', 'criteria', 'self_assessment', 'external_lab'],
            'config_schema': {
                **assignment.manifest['config_schema'],
                'required': ['instructions', 'criteria', 'lab_url'],
                'properties': {
                    **assignment.manifest['config_schema']['properties'],
                    'lab_url': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                    'lab_language': {'type': 'string', 'enum': list(LANGUAGES)},
                },
            },
        }

        def validate_config(self, config):
            if not isinstance(config, dict):
                raise ValidationError({'config': 'Некорректные настройки задания'})
            if not valid_lab_url(config.get('lab_url')):
                raise ValidationError({'lab_url': 'Укажите HTTPS-адрес лаборатории (для разработки допустим http://localhost)'})
            language = config.get('lab_language', 'ru')
            if language not in LANGUAGES:
                raise ValidationError({'lab_language': 'Язык лаборатории: ru, kk или en'})
            rest = {key: value for key, value in config.items() if key not in {'lab_url', 'lab_language'}}
            super().validate_config(rest)

        def evaluate(self, version, data):
            expected = {'checks', 'lab_opened', 'note'}
            if not isinstance(data, dict) or set(data) != expected or data['lab_opened'] is not True:
                raise ValidationError({'submission_data': 'Откройте лабораторию, выполните задание и пройдите самопроверку'})
            note = data['note']
            if not isinstance(note, str) or len(note) > 2000:
                raise ValidationError({'note': 'Заметка: не более 2000 символов'})
            result = checklist_class.evaluate(self, version, {'checks': data['checks']})
            if not result.completed:
                raise ValidationError({'checks': 'Подтвердите все обязательные критерии самопроверки'})
            return replace(result, data={**data, 'note': note.strip()})

    return BiometricLabPlugin()
