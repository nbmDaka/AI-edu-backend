"""Shared practice lifecycle for all AI teaching workspaces."""
from rest_framework.exceptions import ValidationError


def make_workspace_plugin(checklist_class, constructor):
    from ..assignment import make_assignment_plugin
    assignment_plugin = make_assignment_plugin(checklist_class)
    class WorkspacePlugin(checklist_class):
        workspace_type = constructor.document_type
        section_ids = constructor.sections
        workspace_schema_version = 1
        manifest = {
            **checklist_class.manifest,
            'type': constructor.practice_type, 'version': '1.0.0',
            'name': constructor.name,
            'description': constructor.description,
            'icon': 'sparkles', 'frontend_editor': constructor.practice_type, 'student_runtime': constructor.practice_type,
            'grading': 'self_assessment',
            'capabilities': ['instructions', 'criteria', 'self_assessment', 'workspace', 'ai_generation', 'chat'],
            'config_schema': assignment_plugin.manifest['config_schema'],
        }

        def validate_config(self, config):
            if constructor.document_type != 'lecture':
                return assignment_plugin.validate_config(config)
            if config == {}:
                return
            if isinstance(config, dict) and set(config) == {'criteria'}:
                return super().validate_config(config)  # Historical published versions.
            assignment_plugin.validate_config(config)

        def materialize_config(self, version):
            if version.config:
                super().materialize_config(version)

        validate_workspace_parameters = staticmethod(constructor.validate_parameters)
        validate_workspace_content = staticmethod(constructor.validate_content)

        workspace_prompt = staticmethod(constructor.prompt)

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

    return WorkspacePlugin()
