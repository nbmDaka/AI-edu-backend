"""Source-based assignments with a learner report and explicit self-assessment."""
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from rest_framework.exceptions import ValidationError
from apps.mediafiles.models import MediaFile
from apps.mediafiles.uploads import ATTACHMENT_TYPES



def make_assignment_plugin(checklist_class):
    class GuidedAssignmentPlugin(checklist_class):
        # pylint: disable=too-many-ancestors
        manifest = {
            **checklist_class.manifest,
            'type': 'guided_assignment', 'version': '1.0.0',
            'name': 'Практическое задание',
            'description': 'Инструкция, результат работы и самопроверка по критериям',
            'frontend_editor': 'guided_assignment', 'student_runtime': 'guided_assignment',
            'capabilities': ['instructions', 'report', 'criteria', 'self_assessment'],
            'config_schema': {
                'type': 'object', 'additionalProperties': False,
                'required': ['instructions', 'criteria'],
                'properties': {
                    'instructions': {'type': 'array', 'minItems': 1, 'maxItems': 10,
                                     'items': {'type': 'string', 'maxLength': 50000}},
                    'criteria': checklist_class.manifest['config_schema']['properties']['criteria'],
                    'source_url': {'type': 'string', 'maxLength': 2000},
                    'source_files': {'type': 'array', 'maxItems': 20, 'items': {
                        'type': 'object', 'required': ['media', 'name', 'size', 'url'],
                        'properties': {'media': {'type': 'integer'}, 'name': {'type': 'string'},
                                       'size': {'type': 'integer'}, 'url': {'type': 'string'}},
                    }},
                },
            },
        }

        def validate_config(self, config):
            if not isinstance(config, dict) or set(config) - {'instructions', 'criteria', 'source_url', 'source_files'}:
                raise ValidationError({'config': 'Некорректные настройки задания'})
            super().validate_config({'criteria': config.get('criteria')})
            files = config.get('source_files', [])
            if not isinstance(files, list) or len(files) > 20:
                raise ValidationError({'source_files': 'Можно добавить не более 20 файлов'})
            seen = set()
            for file in files:
                if not isinstance(file, dict) or type(file.get('media')) is not int or file['media'] in seen:
                    raise ValidationError({'source_files': 'Некорректный файл источника'})
                seen.add(file['media'])
                media = MediaFile.objects.filter(pk=file['media']).first()
                if (not media or not 0 < media.size <= settings.MAX_UPLOAD_BYTES
                        or Path(media.original_name).suffix.lower() not in ATTACHMENT_TYPES
                        or file.get('name') != media.original_name or file.get('size') != media.size
                        or file.get('url') != f'/api/v1/media/{media.pk}/download/'):
                    raise ValidationError({'source_files': 'Выберите загруженный файл размером до 10 МБ'})
            instructions = config.get('instructions')
            if not isinstance(instructions, list) or not 1 <= len(instructions) <= 10:
                raise ValidationError({'instructions': 'Добавьте инструкцию к заданию'})
            budget = [0]

            def visit(node, depth=0):
                budget[0] += 1
                if (not isinstance(node, dict) or depth > 16 or budget[0] > 10000
                        or node.get('type') not in {'doc', 'paragraph', 'heading', 'text', 'hardBreak',
                            'bulletList', 'orderedList', 'listItem', 'blockquote', 'codeBlock',
                            'table', 'tableRow', 'tableCell', 'tableHeader', 'horizontalRule',
                            'practiceImage', 'inlineMath', 'blockMath'}):
                    raise ValueError()
                if 'text' in node and not isinstance(node['text'], str):
                    raise ValueError()
                if not isinstance(node.get('attrs', {}), dict):
                    raise ValueError()
                if node['type'] == 'practiceImage':
                    media_id = node.get('attrs', {}).get('media')
                    if type(media_id) is not int or not MediaFile.objects.filter(pk=media_id, content_type__startswith='image/').exists():
                        raise ValueError()
                    if not isinstance(node['attrs'].get('caption', ''), str):
                        raise ValueError()
                if node['type'] in {'inlineMath', 'blockMath'} and not isinstance(node.get('attrs', {}).get('latex'), str):
                    raise ValueError()
                marks = node.get('marks', [])
                if not isinstance(marks, list) or len(marks) > 10:
                    raise ValueError()
                if any(not isinstance(mark, dict) or not isinstance(mark.get('type'), str)
                       or not isinstance(mark.get('attrs', {}), dict) for mark in marks):
                    raise ValueError()
                children = node.get('content', [])
                if not isinstance(children, list):
                    raise ValueError()
                for child in children:
                    visit(child, depth + 1)

            try:
                if sum(len(value) for value in instructions) > 200000:
                    raise ValueError()
                for value in instructions:
                    if not isinstance(value, str) or not value.strip() or len(value) > 50000:
                        raise ValueError()
                    document = json.loads(value)
                    visit(document)
                    if document['type'] != 'doc' or not document.get('content'):
                        raise ValueError()
            except (ValueError, TypeError, KeyError, RecursionError):
                raise ValidationError({'instructions': 'Ожидается документ с текстом, списками или таблицами'}) from None
            source = config.get('source_url', '')
            if not isinstance(source, str) or len(source) > 2000:
                raise ValidationError({'source_url': 'Некорректный адрес источника'})
            if source:
                try:
                    parsed = urlsplit(source)
                except ValueError:
                    raise ValidationError({'source_url': 'Некорректный адрес источника'}) from None
                if parsed.scheme != 'https' or parsed.hostname not in ('docs.google.com', 'drive.google.com') or parsed.username:
                    raise ValidationError({'source_url': 'Укажите ссылку на исходный документ Google Drive'})

        def evaluate(self, version, data):
            if not isinstance(data, dict) or set(data) != {'checks', 'report', 'artifact_url'}:
                raise ValidationError({'submission_data': 'Отправьте отчёт и самопроверку'})
            report, artifact = data['report'], data['artifact_url']
            if not isinstance(report, str) or not 40 <= len(report.strip()) <= 20000:
                raise ValidationError({'report': 'Опишите результат и собственные исправления: от 40 до 20000 символов'})
            if not isinstance(artifact, str) or len(artifact) > 2000:
                raise ValidationError({'artifact_url': 'Некорректная ссылка на результат'})
            if artifact:
                from rest_framework import serializers
                serializers.URLField(max_length=2000).run_validation(artifact)
                parsed = urlsplit(artifact)
                if parsed.scheme != 'https' or parsed.username or parsed.password:
                    raise ValidationError({'artifact_url': 'Используйте HTTPS-ссылку без логина и пароля'})
            result = super().evaluate(version, {'checks': data['checks']})
            if not result.completed:
                raise ValidationError({'checks': 'Подтвердите все обязательные критерии самопроверки'})
            return replace(result, data=data)

    return GuidedAssignmentPlugin()
