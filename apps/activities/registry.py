"""Explicit plugin registration. API manifests are the catalog's source of truth."""
from dataclasses import dataclass
from typing import Protocol

from rest_framework.exceptions import ValidationError


@dataclass(frozen=True)
class Evaluation:
    score: float
    source: str
    completed: bool
    snapshot: list
    data: dict


class PracticePlugin(Protocol):
    manifest: dict
    def validate_config(self, config): ...
    def materialize_config(self, version): ...
    def evaluate(self, version, data) -> Evaluation: ...


class PracticeRegistry:
    def __init__(self):
        self._plugins = {}

    def register(self, plugin: PracticePlugin):
        key = (plugin.manifest['type'], plugin.manifest['version'])
        if key in self._plugins:
            raise ValueError(f'Duplicate practice plugin: {key}')
        self._plugins[key] = plugin

    def get(self, code, version='1.0.0'):
        plugin = self._plugins.get((code, version))
        if not plugin:
            raise ValidationError({'practice_type': 'Тип или версия практики недоступны'})
        return plugin

    def manifests(self):
        return [plugin.manifest for plugin in self._plugins.values() if plugin.manifest['available']]


class ChecklistPlugin:
    manifest = {
        'type': 'checklist_practice', 'version': '1.0.0',
        'name': 'Практика с самооценкой',
        'description': 'Критерии выполнения и Ψ', 'icon': 'list-checks',
        'grading': 'self_assessment', 'capabilities': ['criteria', 'self_assessment'],
        'available': True, 'frontend_editor': 'checklist_practice', 'student_runtime': 'checklist_practice',
        'config_schema': {
            'type': 'object', 'additionalProperties': False, 'required': ['criteria'],
            'properties': {'criteria': {'type': 'array', 'minItems': 1, 'maxItems': 30,
                'items': {'type': 'object', 'additionalProperties': False, 'required': ['title', 'description', 'is_required'],
                    'properties': {'title': {'type': 'string', 'minLength': 1, 'maxLength': 300},
                                   'description': {'type': 'string', 'maxLength': 2000}, 'is_required': {'type': 'boolean'}}}}},
        },
    }

    def validate_config(self, config):
        # Keep this validator aligned with the exported schema; no coercion of JSON values.
        if not isinstance(config, dict) or set(config) != {'criteria'}:
            raise ValidationError({'config': 'Ожидается объект с критериями'})
        criteria = config['criteria']
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 30:
            raise ValidationError({'config': 'Укажите от 1 до 30 критериев'})
        for criterion in criteria:
            if (not isinstance(criterion, dict) or set(criterion) != {'title', 'description', 'is_required'} or
                not isinstance(criterion['title'], str) or not criterion['title'].strip() or len(criterion['title']) > 300 or
                not isinstance(criterion['description'], str) or len(criterion['description']) > 2000 or
                type(criterion['is_required']) is not bool):
                raise ValidationError({'config': 'Некорректный критерий самопроверки'})

    def materialize_config(self, version):
        from .models import PracticeCriterion
        for position, criterion in enumerate(version.config['criteria']):
            PracticeCriterion.objects.create(practice_version=version, position=position, **criterion)

    def evaluate(self, version, data):
        criteria = list(version.criteria.all())
        if not isinstance(data, dict) or set(data) != {'checks'}:
            raise ValidationError({'submission_data': 'Отправьте только состояние критериев'})
        checks = data['checks']
        if (not isinstance(checks, list) or len(checks) != len(criteria) or not criteria or
                any(type(check) is not bool for check in checks)):
            raise ValidationError({'checks': 'Заполните все критерии логическими значениями'})
        snapshot = [{'id': row.pk, 'title': row.title, 'description': row.description,
                     'position': row.position, 'is_required': row.is_required, 'checked': checked}
                    for row, checked in zip(criteria, checks)]
        return Evaluation(sum(checks) / len(criteria), 'SELF_ASSESSMENT',
                          all(checked for row, checked in zip(criteria, checks) if row.is_required), snapshot, data)


registry = PracticeRegistry()

from .workspace.lecture import make_lecture_plugin
lecture_plugin = make_lecture_plugin(ChecklistPlugin)
registry.register(lecture_plugin)

from .assignment import make_assignment_plugin
registry.register(make_assignment_plugin(ChecklistPlugin))

# Keep pinned historical assignments usable with the same chat runtime.
class LegacyPracticePlugin(type(lecture_plugin)):
    manifest = {**lecture_plugin.manifest, 'type': 'checklist_practice', 'available': False}
    def validate_config(self, config):
        if config == {}:
            return ChecklistPlugin.validate_config(self, config)
        return super().validate_config(config)

registry.register(LegacyPracticePlugin())
