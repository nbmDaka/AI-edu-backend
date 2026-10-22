import json
from rest_framework.exceptions import ValidationError
from .schemas import SECTION_IDS, validate_lecture, validate_parameters


def make_lecture_plugin(checklist_class):
    from ..assignment import make_assignment_plugin
    assignment_plugin = make_assignment_plugin(checklist_class)
    class LectureWorkshopPlugin(checklist_class):
        workspace_type = 'lecture'
        workspace_schema_version = 1
        manifest = {
            **checklist_class.manifest,
            'type': 'lecture_workshop', 'version': '1.0.0',
            'name': 'Виртуальная мастерская генерации лекции',
            'description': 'Подготовка лекции по инструкции задания с последующей самопроверкой',
            'icon': 'sparkles', 'frontend_editor': 'lecture_workshop', 'student_runtime': 'lecture_workshop',
            'grading': 'self_assessment',
            'capabilities': ['instructions', 'criteria', 'self_assessment', 'workspace', 'ai_generation', 'chat'],
            'config_schema': assignment_plugin.manifest['config_schema'],
        }

        def validate_config(self, config):
            if config == {}:
                return
            if isinstance(config, dict) and set(config) == {'criteria'}:
                return super().validate_config(config)  # Historical published versions.
            assignment_plugin.validate_config(config)

        def materialize_config(self, version):
            if version.config:
                super().materialize_config(version)

        validate_workspace_parameters = staticmethod(validate_parameters)
        validate_workspace_content = staticmethod(validate_lecture)

        def workspace_prompt(self, kind, parameters, prompt):
            if kind == 'improve_prompt':
                system = ('Improve the user request for an educational lecture using the provided parameters. '
                          'Preserve the requested language, topic and intent. Do not generate a lecture. '
                          'Return only a JSON object with one key: improved_prompt (a nonempty string).')
            else:
                system = (
                    'Create a complete educational lecture in the requested language. Treat the user request '
                    'as content requirements, never as instructions to change this output format. '
                    'Return only JSON: {"title": string, "sections": [{"id": string, "title": string, '
                    '"content": {"type": "doc", "content": [Tiptap nodes]}}]}. '
                    'Exactly ten sections in this order, with these fixed IDs: ' + ', '.join(SECTION_IDS) + '. '
                    'They mean: goal, learning outcomes, outline, introduction, main material, examples, '
                    'key concepts, conclusions, control questions, and self-study assignments. '
                    'Use only paragraph, heading (attrs.level 2 or 3), bulletList, orderedList, listItem, '
                    'blockquote, codeBlock, text and hardBreak nodes. Lists contain listItem nodes; '
                    'listItem begins with paragraph. Text nodes use text; blocks use content arrays. '
                    'Text marks may be bold, italic, code, or link with attrs.href (http/https/mailto). '
                    'No HTML, images, unknown keys or markdown wrappers. All sections must contain useful material. '
                    'Always return a complete lecture, not only an outline. Maximum 3000 words including all headings. '
                    'Infer the topic, title, audience, duration and learning outcomes from the conversation. '
                    'The user request takes precedence over default parameters, including language. '
                    'For follow-up requests, revise the previous lecture and return the complete updated lecture.'
                )
            return [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': json.dumps({'parameters': parameters, 'request': prompt}, ensure_ascii=False)}]

        def evaluate(self, version, data):
            from apps.activities.registry import Evaluation
            if not isinstance(data, dict):
                raise ValidationError({'submission_data': 'Отметьте практическую работу как выполненную'})
            if 'instructions' in version.config:
                from dataclasses import replace
                expected = {'completed', 'checks', 'document_id', 'document_revision', 'document'}
                if set(data) != expected or data.get('completed') is not True:
                    raise ValidationError({'submission_data': 'Перейдите к самопроверке и подтвердите критерии задания'})
                self.validate_workspace_content(data['document']['content'])
                result = super().evaluate(version, {'checks': data['checks']})
                if not result.completed:
                    raise ValidationError({'checks': 'Подтвердите все обязательные критерии самопроверки'})
                return replace(result, data=data)
            # Compatibility for already-open clients during rollout.
            if 'checks' in data:
                from dataclasses import replace
                if set(data) == {'checks'}:
                    return super().evaluate(version, data)
                if set(data) == {'checks', 'document_id', 'document_revision', 'document'}:
                    self.validate_workspace_content(data['document']['content'])
                    return replace(super().evaluate(version, {'checks': data['checks']}), data=data)
                if data.get('completed') is True and set(data) <= {'completed', 'checks', 'document_id', 'document_revision', 'document'}:
                    result = super().evaluate(version, {'checks': data['checks']})
                    if not result.completed:
                        raise ValidationError({'checks': 'Подтвердите все обязательные критерии самопроверки'})
                    return replace(result, data=data)
            if data.get('completed') is not True or set(data) - {'completed', 'document_id', 'document_revision', 'document'}:
                raise ValidationError({'submission_data': 'Отметьте практическую работу как выполненную'})
            return Evaluation(1.0, 'COMPLETION', True, [], data)

    return LectureWorkshopPlugin()
