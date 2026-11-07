"""Python practice: code runs in the learner's browser (Pyodide), the server compares check values."""
import math
from pathlib import Path

from django.conf import settings
from rest_framework.exceptions import ValidationError

from apps.mediafiles.models import MediaFile

from .registry import Evaluation

PACKAGES = ('numpy', 'pandas', 'scikit-learn', 'matplotlib', 'scipy')
DATASET_TYPES = ('.csv', '.xlsx', '.txt')
MAX_STEPS, MAX_CHECKS, MAX_LIST = 15, 50, 100
MAX_CODE, MAX_TEXT, MAX_EXPRESSION = 20000, 20000, 500

SCALAR = {'type': ['number', 'string', 'boolean', 'null']}
CHECK_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['title', 'expression', 'expected', 'tolerance', 'is_required'],
    'properties': {
        'title': {'type': 'string', 'minLength': 1, 'maxLength': 300},
        'expression': {'type': 'string', 'minLength': 1, 'maxLength': MAX_EXPRESSION},
        'expected': {'anyOf': [SCALAR, {'type': 'array', 'maxItems': MAX_LIST, 'items': SCALAR}]},
        'tolerance': {'type': 'number', 'minimum': 0},
        'is_required': {'type': 'boolean'},
    },
}
CONFIG_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['steps', 'datasets', 'packages'],
    'properties': {
        'steps': {'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['title', 'instructions', 'starter_code', 'checks'],
            'properties': {
                'title': {'type': 'string', 'minLength': 1, 'maxLength': 300},
                'instructions': {'type': 'string', 'maxLength': MAX_TEXT},
                'starter_code': {'type': 'string', 'maxLength': MAX_CODE},
                'checks': {'type': 'array', 'maxItems': MAX_CHECKS, 'items': CHECK_SCHEMA},
            }}},
        'datasets': {'type': 'array', 'maxItems': 5, 'items': {
            'type': 'object', 'required': ['media', 'name', 'size', 'url'],
            'properties': {'media': {'type': 'integer'}, 'name': {'type': 'string'},
                           'size': {'type': 'integer'}, 'url': {'type': 'string'}}}},
        'packages': {'type': 'array', 'uniqueItems': True, 'items': {'enum': list(PACKAGES)}},
    },
}


def is_scalar(value):
    return value is None or type(value) in (bool, int, str) or (type(value) is float and math.isfinite(value))


def is_value(value):
    return is_scalar(value) or (isinstance(value, list) and len(value) <= MAX_LIST and all(map(is_scalar, value)))


def same(expected, actual, tolerance):
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(same(e, a, tolerance) for e, a in zip(expected, actual)))
    numeric = (int, float)
    if type(expected) in numeric and type(actual) in numeric:
        return abs(expected - actual) <= tolerance
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip() == actual.strip()
    return type(expected) is type(actual) and expected == actual


def checks_of(config):
    return [(index, check) for index, step in enumerate(config['steps']) for check in step['checks']]


def make_python_lab_plugin(checklist_class):
    class PythonLabPlugin(checklist_class):
        # pylint: disable=too-many-ancestors
        manifest = {
            **checklist_class.manifest,
            'type': 'python_lab', 'version': '1.0.0',
            'name': 'Практика на Python',
            'description': 'Код на Python в браузере, автоматическая проверка результатов',
            'icon': 'code', 'grading': 'auto_grading',
            'capabilities': ['instructions', 'code', 'datasets', 'auto_grading'],
            'frontend_editor': 'python_lab', 'student_runtime': 'python_lab',
            'config_schema': CONFIG_SCHEMA,
        }

        def validate_config(self, config):
            if not isinstance(config, dict) or set(config) != {'steps', 'datasets', 'packages'}:
                raise ValidationError({'config': 'Ожидаются шаги, наборы данных и пакеты'})
            steps = config['steps']
            if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
                raise ValidationError({'steps': f'Добавьте от 1 до {MAX_STEPS} шагов'})
            for step in steps:
                if (not isinstance(step, dict) or set(step) != {'title', 'instructions', 'starter_code', 'checks'}
                        or not isinstance(step['title'], str) or not step['title'].strip() or len(step['title']) > 300
                        or not isinstance(step['instructions'], str) or len(step['instructions']) > MAX_TEXT
                        or not isinstance(step['starter_code'], str) or len(step['starter_code']) > MAX_CODE
                        or not isinstance(step['checks'], list)):
                    raise ValidationError({'steps': 'Некорректный шаг практики'})
                for check in step['checks']:
                    if (not isinstance(check, dict)
                            or set(check) != {'title', 'expression', 'expected', 'tolerance', 'is_required'}
                            or not isinstance(check['title'], str) or not check['title'].strip()
                            or len(check['title']) > 300
                            or not isinstance(check['expression'], str) or not check['expression'].strip()
                            or len(check['expression']) > MAX_EXPRESSION or '\n' in check['expression']
                            or not is_value(check['expected'])
                            or type(check['tolerance']) not in (int, float) or not math.isfinite(check['tolerance'])
                            or check['tolerance'] < 0 or type(check['is_required']) is not bool):
                        raise ValidationError({'checks': 'Некорректная проверка: нужно выражение, ожидаемое значение и допуск'})
            total = len(checks_of(config))
            if not 1 <= total <= MAX_CHECKS:
                raise ValidationError({'checks': f'Добавьте от 1 до {MAX_CHECKS} проверок'})
            packages = config['packages']
            if (not isinstance(packages, list) or len(set(packages)) != len(packages)
                    or any(package not in PACKAGES for package in packages)):
                raise ValidationError({'packages': 'Недопустимый пакет Python'})
            datasets = config['datasets']
            if not isinstance(datasets, list) or len(datasets) > 5:
                raise ValidationError({'datasets': 'Можно добавить не более 5 наборов данных'})
            seen = set()
            for file in datasets:
                if not isinstance(file, dict) or type(file.get('media')) is not int or file['media'] in seen:
                    raise ValidationError({'datasets': 'Некорректный набор данных'})
                seen.add(file['media'])
                media = MediaFile.objects.filter(pk=file['media']).first()
                if (not media or not 0 < media.size <= settings.MAX_UPLOAD_BYTES
                        or Path(media.original_name).suffix.lower() not in DATASET_TYPES
                        or file['name'] != media.original_name or file['size'] != media.size
                        or file['url'] != f'/api/v1/media/{media.pk}/download/'):
                    raise ValidationError({'datasets': 'Выберите загруженный CSV, XLSX или TXT размером до 10 МБ'})

        def materialize_config(self, version):
            from .models import PracticeCriterion
            for position, (_, check) in enumerate(checks_of(version.config)):
                PracticeCriterion.objects.create(practice_version=version, position=position, title=check['title'],
                                                 description='', is_required=check['is_required'])

        def evaluate(self, version, data):
            steps = version.config['steps']
            checks = checks_of(version.config)
            if not isinstance(data, dict) or set(data) != {'code', 'results'}:
                raise ValidationError({'submission_data': 'Отправьте код шагов и результаты проверок'})
            code, results = data['code'], data['results']
            if (not isinstance(code, list) or len(code) != len(steps)
                    or any(not isinstance(text, str) or len(text) > MAX_CODE for text in code)):
                raise ValidationError({'code': 'Отправьте код каждого шага, не длиннее 20000 символов'})
            if not isinstance(results, list) or len(results) != len(checks):
                raise ValidationError({'results': 'Запустите код, чтобы получить результаты всех проверок'})
            for result in results:
                if not (isinstance(result, dict) and (
                        (set(result) == {'value'} and is_value(result['value']))
                        or (set(result) == {'error'} and isinstance(result['error'], str) and len(result['error']) <= 2000))):
                    raise ValidationError({'results': 'Некорректный результат проверки'})
            snapshot = []
            for position, ((step, check), result) in enumerate(zip(checks, results)):
                passed = 'value' in result and same(check['expected'], result['value'], check['tolerance'])
                snapshot.append({'title': check['title'], 'description': '', 'position': position, 'step': step,
                                 'is_required': check['is_required'], 'checked': passed,
                                 'actual': result.get('value'), 'error': result.get('error', '')})
            score = sum(row['checked'] for row in snapshot) / len(snapshot)
            completed = all(row['checked'] for row in snapshot if row['is_required'])
            return Evaluation(score, 'AUTO_GRADING', completed, snapshot, data)

    return PythonLabPlugin()
