import json
import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from apps.education.models import LearningItem
from .registry import registry
from .services import save_draft, publish_draft
from .test_workspace import WorkspaceFixture, lecture, ai_response
from .test_workspace_chat import plan
from .workspace.constructors import CONSTRUCTORS
from .workspace.services import create_document, create_operation
from .workspace.worker import claim_next_operation, execute_operation


def content_for(spec):
    return {'title': spec.name, 'sections': [
        {**section, 'id': key, 'title': key} for section, key in zip(lecture()['sections'], spec.sections)]}


@override_settings(AI_BASE_URL='https://provider.test/v1', AI_MODEL='test-model')
class ConstructorTests(WorkspaceFixture, TestCase):
    def constructor_document(self, spec):
        item = LearningItem.objects.create(title=spec.name, module=self.module, type='PRACTICE',
                                           position=LearningItem.objects.filter(module=self.module).count())
        config = {**self.config, 'instructions': [json.dumps({'type': 'doc', 'content': [{'type': 'paragraph',
            'content': [{'type': 'text', 'text': 'Подготовьте материал по теме задания'}]}]})]}
        save_draft(item, self.user, {**self.draft, 'practice_type': spec.practice_type, 'config': config})
        publish_draft(item)
        item.refresh_from_db()
        return create_document(self.user, item, self.parameters)[0]

    def run_chat(self, document, responses, **extra):
        document.refresh_from_db()
        operation = create_operation(self.user, document, {'request_id': uuid.uuid4(), 'revision': document.revision,
                                     'kind': 'chat', 'prompt': 'Подготовь материал', **extra})
        claim = claim_next_operation()
        self.assertEqual(claim.pk, operation.pk)
        with patch('apps.activities.workspace.conversation.run_ai', side_effect=responses) as provider:
            execute_operation(claim)
        operation.refresh_from_db()
        document.refresh_from_db()
        self.assertEqual(operation.status, 'SUCCEEDED', operation.error_code)
        return operation, provider

    def test_all_constructors_generate_restore_export_and_submit(self):
        for spec in CONSTRUCTORS:
            with self.subTest(kind=spec.practice_type):
                document = self.constructor_document(spec)
                content = content_for(spec)
                operation, provider = self.run_chat(document, [plan(docx=True), ai_response(content)])
                self.assertEqual(document.document_type, spec.document_type)
                self.assertEqual(document.content, content)
                prompt = provider.call_args_list[1].args[0].input_data['messages'][0]['content']
                self.assertIn(spec.instructions, prompt)
                self.assertIn(', '.join(spec.sections), prompt)
                self.assertEqual(self.client.get(operation.result['artifact']['url']).status_code, 200)
                path = f'/api/v1/workspace/documents/{document.pk}/'
                self.assertEqual(self.client.get(path).data['content'], content)
                self.assertEqual(self.client.get(path + 'messages/').data['results'][-1]['content'], content)
                response = self.client.post(f'/api/v1/items/{document.submission.item.short_id}/practice/', {
                    'submission_id': document.submission_id, 'submission_data': {'completed': True, 'checks': [True],
                        'document_id': document.pk, 'document_revision': document.revision}}, format='json')
                self.assertEqual(response.status_code, 200, response.data)

    def test_format_inputs_survive_clarification_and_edits(self):
        spec = CONSTRUCTORS[1]
        requirements = {'session_type': 'business_game'}
        document = self.constructor_document(spec)
        self.run_chat(document, [plan('clarify', message='Какая аудитория?', requirements=requirements)])
        self.run_chat(document, [plan(), ai_response(content_for(spec))])
        for key, value in requirements.items():
            self.assertEqual(document.parameters[key], value)
        changed = {**content_for(spec), 'title': 'Обновлённый материал'}
        _, provider = self.run_chat(document, [plan('edit'), ai_response(changed)])
        self.assertEqual(document.content, changed)
        messages = provider.call_args_list[1].args[0].input_data['messages']
        self.assertIn(content_for(spec), [json.loads(m['content']) for m in messages if m['role'] == 'assistant'])

    def test_wrong_document_contract_and_invalid_parameters_are_rejected(self):
        for spec in CONSTRUCTORS:
            plugin = registry.get(spec.practice_type)
            with self.assertRaises(ValidationError):
                plugin.validate_workspace_content(lecture())
            with self.assertRaises(ValidationError):
                plugin.validate_workspace_parameters({**self.parameters, 'duration_minutes': 0})
            with self.assertRaises(ValidationError):
                plugin.validate_workspace_parameters({**self.parameters, 'session_type': 'invalid'})

    def test_source_lecture_snapshot_is_used_for_generation_and_follow_up(self):
        self.document.content = lecture()
        self.document.save()
        document = self.constructor_document(CONSTRUCTORS[0])
        _, provider = self.run_chat(document, [plan(), ai_response(content_for(CONSTRUCTORS[0]))], source_lecture_id=self.document.pk)
        for call in provider.call_args_list:
            self.assertIn('Лекция о педагогике', json.dumps(call.args[0].input_data['messages'], ensure_ascii=False))
        self.document.content = {**lecture(), 'title': 'Changed after the request'}
        self.document.save()
        _, provider = self.run_chat(document, [plan('edit'), ai_response(content_for(CONSTRUCTORS[0]))])
        prompt = json.dumps(provider.call_args_list[1].args[0].input_data['messages'], ensure_ascii=False)
        self.assertIn('Лекция о педагогике', prompt)
        self.assertNotIn('Changed after the request', prompt)

    def test_pasted_lecture_is_used_directly_and_prevents_source_offer_during_clarification(self):
        document = self.constructor_document(CONSTRUCTORS[0])
        text = 'Составь практику по лекции: Формирующее оценивание выявляет пробелы. Обратная связь направляет обучение.'
        operation, provider = self.run_chat(document, [plan('clarify', message='На сколько минут?',
            has_lecture_text=True, offer_source_lecture=True)], prompt=text)
        self.assertFalse(operation.result.get('offer_source_lecture'))
        self.assertTrue(document.conversation_state['has_lecture_text'])
        system = provider.call_args.args[0].input_data['messages'][0]['content']
        self.assertIn('Inspect the user request and conversation semantically', system)
        self.assertIn('NEVER ask to select, save', system)
        # The detected inline source survives subsequent clarification, even if omitted or reset by the model.
        operation, _ = self.run_chat(document, [plan('clarify', message='Для какой аудитории?',
            has_lecture_text=False, offer_source_lecture=True)])
        self.assertFalse(operation.result.get('offer_source_lecture'))
        _, provider = self.run_chat(document, [plan(), ai_response(content_for(CONSTRUCTORS[0]))])
        self.assertIn(text, json.dumps(provider.call_args_list[1].args[0].input_data['messages'], ensure_ascii=False))
        output = self.client.get(f'/api/v1/workspace/documents/{document.pk}/messages/').data['results']
        self.assertFalse(any(message['offer_source_lecture'] for message in output))

    def test_source_offer_is_saved_only_for_missing_lecture_clarification(self):
        document = self.constructor_document(CONSTRUCTORS[0])
        operation, _ = self.run_chat(document, [plan('clarify', message='Вставьте текст, выберите лекцию или продолжите по теме.',
            has_lecture_text=False, offer_source_lecture=True)])
        self.assertTrue(operation.result['offer_source_lecture'])
        output = self.client.get(f'/api/v1/workspace/documents/{document.pk}/messages/').data['results']
        self.assertTrue(output[-1]['offer_source_lecture'])
        self.assertFalse(output[-2]['offer_source_lecture'])
        # A user may continue with the topic alone, without selecting or saving a lecture.
        operation, _ = self.run_chat(document, [plan(offer_source_lecture=True), ai_response(content_for(CONSTRUCTORS[0]))],
                                     prompt='Продолжи только по теме, без лекции')
        self.assertFalse(operation.result.get('offer_source_lecture'))

    def test_saved_lecture_does_not_trigger_a_second_source_offer(self):
        self.document.content = lecture()
        self.document.save()
        document = self.constructor_document(CONSTRUCTORS[0])
        operation, _ = self.run_chat(document, [plan('clarify', message='Для какой аудитории?', offer_source_lecture=True)],
                                     source_lecture_id=self.document.pk)
        self.assertFalse(operation.result.get('offer_source_lecture'))

    def test_source_catalog_and_selection_are_private_and_access_checked(self):
        self.document.content = lecture()
        self.document.save()
        path = '/api/v1/workspace/source-lectures/'
        self.assertEqual([row['id'] for row in self.client.get(path).data['results']], [self.document.pk])
        document = self.constructor_document(CONSTRUCTORS[0])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(path).data['results'], [])
        other_document, _ = create_document(self.other, document.submission.item, self.parameters)
        data = {'request_id': str(uuid.uuid4()), 'revision': 1, 'kind': 'chat', 'prompt': 'Подготовь занятие',
                'source_lecture_id': self.document.pk}
        response = self.client.post(f'/api/v1/workspace/documents/{other_document.pk}/operations/', data, format='json')
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.user)
        self.user.course_access_mode = 'CUSTOM'
        self.user.save()
        self.assertEqual(self.client.get(path).data['results'], [])

    def test_source_survives_retry_after_failed_first_generation(self):
        self.document.content = lecture()
        self.document.save()
        document = self.constructor_document(CONSTRUCTORS[0])
        operation = create_operation(self.user, document, {'request_id': uuid.uuid4(), 'revision': 1,
            'kind': 'chat', 'prompt': 'Подготовь занятие', 'source_lecture_id': self.document.pk})
        with patch('apps.activities.workspace.conversation.run_ai', side_effect=[plan(), ai_response(lecture())]):
            execute_operation(claim_next_operation())
        operation.refresh_from_db()
        self.assertEqual(operation.status, 'FAILED')
        _, provider = self.run_chat(document, [plan(), ai_response(content_for(CONSTRUCTORS[0]))])
        self.assertIn('Лекция о педагогике', json.dumps(provider.call_args_list[1].args[0].input_data['messages'], ensure_ascii=False))

    def test_source_is_part_of_idempotency_and_access_is_rechecked_by_worker(self):
        self.document.content = lecture()
        self.document.save()
        document = self.constructor_document(CONSTRUCTORS[0])
        path = f'/api/v1/workspace/documents/{document.pk}/operations/'
        data = {'request_id': str(uuid.uuid4()), 'revision': 1, 'kind': 'chat', 'prompt': 'Подготовь занятие',
                'source_lecture_id': self.document.pk}
        first = self.client.post(path, data, format='json')
        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(self.client.post(path, data, format='json').data['id'], first.data['id'])
        self.assertEqual(self.client.post(path, {**data, 'source_lecture_id': 999999}, format='json').status_code, 409)
        # Revoking the source must prevent its queued use even if the target remains accessible.
        self.document.submission.item.status = 'DRAFT'
        self.document.submission.item.save()
        with patch('apps.activities.workspace.conversation.run_ai') as provider:
            execute_operation(claim_next_operation())
        self.assertFalse(provider.called)
        document.refresh_from_db()
        self.assertEqual(document.content, {})
