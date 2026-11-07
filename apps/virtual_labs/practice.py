"""Регистрация виртуальных работ как практик AI edu (Practice Engine).

В AI edu каждая практика — плагин с манифестом, проверкой настроек и серверным оцениванием
(apps/activities/registry.py). Модуль регистрирует работы из VIRTUAL_LABS['PRACTICE_WORKS']
(по умолчанию № 4–14: № 1–3 в AI edu уже есть) с типами virtual_lab_vw04 … virtual_lab_vw14.

Настройки задания (инструкция, критерии самопроверки, файлы-источники) такие же, как у
«Практического задания» AI edu, поэтому администратор редактирует их привычным редактором.
При сдаче сервер заново проводит AI-экспертизу итогового материала по 10 критериям и
сохраняет балл как AUTO_GRADING (0–1). Обязательные критерии самопроверки должны быть отмечены.

В автономном запуске (без AI edu) регистрация просто пропускается.
"""
import logging
from dataclasses import replace

from . import catalog
from .lab_settings import conf

log = logging.getLogger(__name__)
PLUGIN_VERSION = '1.0.0'
DATA_KEYS = {'work', 'title', 'text', 'inputs', 'checks', 'session_id', 'portfolio_id'}


def practice_type(code):
    return f'virtual_lab_{code}'


def register_practices():
    try:
        from apps.activities.assignment import make_assignment_plugin
        from apps.activities.registry import ChecklistPlugin, Evaluation, registry
    except ImportError:
        return 0
    from rest_framework.exceptions import ValidationError
    from .services import expertise

    base = type(make_assignment_plugin(ChecklistPlugin))
    count = 0
    for code in conf('PRACTICE_WORKS'):
        meta = catalog.work(code)
        if not meta:
            log.warning('virtual_labs: работа %s не найдена в каталоге', code)
            continue

        class VirtualLabPlugin(base):  # pylint: disable=too-many-ancestors
            work_code = code
            manifest = {
                **base.manifest,
                'type': practice_type(code), 'version': PLUGIN_VERSION,
                'name': f"ВР № {meta['n']}. {meta['title']}",
                'description': meta.get('goal', ''),
                'icon': 'flask-conical',
                'grading': 'auto_grading',
                'capabilities': ['instructions', 'criteria', 'self_assessment', 'ai_generation', 'auto_grading', 'virtual_lab'],
                'frontend_editor': practice_type(code), 'student_runtime': practice_type(code),
            }

            def evaluate(self, version, data):
                if not isinstance(data, dict) or set(data) - DATA_KEYS or data.get('work') != self.work_code:
                    raise ValidationError({'submission_data': 'Сохраните итоговый материал виртуальной работы'})
                text, inputs = data.get('text'), data.get('inputs') or {}
                if not isinstance(text, str) or not 20 <= len(text.strip()) <= 300000 or not isinstance(inputs, dict):
                    raise ValidationError({'text': 'Итоговый материал не найден'})
                checks = data.get('checks', [])
                result = ChecklistPlugin.evaluate(self, version, {'checks': checks}) if version.criteria.exists() \
                    else Evaluation(1.0, 'SELF_ASSESSMENT', True, [], {})
                if not result.completed:
                    raise ValidationError({'checks': 'Подтвердите все обязательные критерии самопроверки'})
                ev = expertise.evaluate_work(self.work_code, text, inputs)
                score = max(0.0, min(1.0, ev['total'] / 100))
                summary = {'total': ev['total'], 'level': ev['level'],
                           'criteria': [{'key': c['key'], 'name': c['name'], 'score': c['score']} for c in ev['criteria']]}
                return replace(result, score=score, source='AUTO_GRADING', data={**data, 'evaluation': summary})

        registry.register(VirtualLabPlugin())
        count += 1
    return count
