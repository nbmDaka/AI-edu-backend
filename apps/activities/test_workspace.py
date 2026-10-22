import json
import subprocess
import uuid
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.db import connection, transaction, close_old_connections
from django.test import TestCase, TransactionTestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.education.models import LearningTrack, Course, Module, LearningItem, PracticeSubmission, UserLearningItemProgress
from .models import WorkspaceDocument, WorkspaceMessage, WorkspaceOperation, PracticeResult
from .services import save_draft, publish_draft
from .workspace.schemas import SECTION_IDS, validate_lecture
from .workspace.services import create_document, create_operation
from .workspace.worker import claim_next_operation, execute_operation, finish_operation, recover_expired_operations
from .workspace.ai import AIError, call_api, run_ai


def lecture():
    return {'title': 'Лекция о педагогике', 'sections': [
        {'id': key, 'title': key, 'content': {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Учебный материал'}]}]}}
        for key in SECTION_IDS]}


def ai_response(payload=None):
    return {'payload': payload if payload is not None else lecture(), 'model': 'actual-model',
            'input_tokens': 100, 'output_tokens': 200, 'total_tokens': 300, 'cost': '0.00100000', 'cost_currency': 'USD'}


class WorkspaceFixture:
    def setUp(self):
        super().setUp()
        self.track = LearningTrack.objects.create(title='Track', is_published=True)
        self.course = Course.objects.create(title='Course', learning_track=self.track, is_published=True)
        self.module = Module.objects.create(title='Module', course=self.course, is_published=True)
        self.item = LearningItem.objects.create(title='Workshop', module=self.module, type='PRACTICE')
        self.user = User.objects.create_user('workspace@test.test', 'Password123!', learning_track=self.track)
        self.other = User.objects.create_user('other@test.test', 'Password123!', learning_track=self.track)
        self.client = APIClient()
        self.client.force_login(self.user)
        self.parameters = {'discipline': 'Педагогика', 'topic': 'Обучение', 'course': '2',
                           'audience': 'Будущие преподаватели', 'duration_minutes': 45, 'difficulty': 'beginner',
                           'learning_outcomes': ['Объяснить методы обучения'], 'language': 'ru'}
        self.config = {'criteria': [{'title': 'Проверено', 'description': '', 'is_required': True}]}
        self.draft = {'practice_type': 'lecture_workshop', 'plugin_version': '1.0.0',
                      'title': self.item.title, 'description': '', 'config': self.config}
        save_draft(self.item, self.user, self.draft)
        publish_draft(self.item)
        self.item.refresh_from_db()
        self.document, _ = create_document(self.user, self.item, self.parameters)
        self.path = f'/api/v1/workspace/documents/{self.document.pk}/'
        self.item_path = f'/api/v1/items/{self.item.short_id}/practice/'

    def operation_data(self, kind='generate', **extra):
        return {'request_id': str(uuid.uuid4()), 'revision': self.document.revision,
                'kind': kind, 'prompt': 'Создай учебную лекцию', **extra}

    def queue(self, kind='generate'):
        data = self.operation_data(kind)
        data['request_id'] = uuid.UUID(data['request_id'])
        return create_operation(self.user, self.document, data)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   AI_BASE_URL='https://provider.test/v1', AI_MODEL='configured-model', AI_API_KEY='private-secret')
class WorkspaceAPITests(WorkspaceFixture, TestCase):
    def test_create_uses_existing_attempt_and_does_not_replace_parameters(self):
        different = {**self.parameters, 'topic': 'Другой запрос'}
        response = self.client.post(self.item_path + 'workspace/', {'parameters': different}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], self.document.pk)
        self.assertEqual(response.data['parameters'], self.parameters)
        self.assertEqual(PracticeSubmission.objects.count(), 1)
        self.assertFalse(UserLearningItemProgress.objects.exists())

    def test_first_create_returns_201_and_owner_is_server_assigned(self):
        self.client.force_login(self.other)
        response = self.client.post(self.item_path + 'workspace/', {'parameters': self.parameters}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(WorkspaceDocument.objects.get(pk=response.data['id']).owner, self.other)

    def test_invalid_parameters_leave_no_new_attempt(self):
        self.client.force_login(self.other)
        response = self.client.post(self.item_path + 'workspace/', {'parameters': {**self.parameters, 'duration_minutes': 0}}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PracticeSubmission.objects.filter(user=self.other).exists())

    def test_authentication_and_csrf_are_required(self):
        self.assertEqual(APIClient().get(self.path).status_code, 403)
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.patch(self.path, {'revision': 1, 'content': lecture()}, format='json').status_code, 403)

    def test_all_resource_endpoints_hide_other_users_documents(self):
        operation = self.queue()
        self.client.force_login(self.other)
        for path in (self.path, self.path + 'messages/', f'/api/v1/workspace/operations/{operation.pk}/'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.patch(self.path, {'revision': 1, 'content': lecture()}, format='json').status_code, 404)
        self.assertEqual(self.client.post(self.path + 'operations/', self.operation_data(), format='json').status_code, 404)

    def test_course_access_is_rechecked_for_existing_documents(self):
        self.user.course_access_mode = 'CUSTOM'
        self.user.save()
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.client.post(self.item_path + 'workspace/', {'parameters': self.parameters}, format='json').status_code, 404)

    def test_locked_module_denies_workspace_creation(self):
        self.course.adaptive_learning_enabled = True
        self.course.save()
        module = Module.objects.create(title='Locked', course=self.course, position=1, is_published=True)
        item = LearningItem.objects.create(title='Locked workshop', module=module, type='PRACTICE')
        save_draft(item, self.user, self.draft)
        publish_draft(item)
        response = self.client.post(f'/api/v1/items/{item.short_id}/practice/workspace/', {'parameters': self.parameters}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PracticeSubmission.objects.filter(item=item).exists())

    def test_draft_or_unpublished_course_is_not_accessible(self):
        for model in (self.item, self.module, self.course, self.track):
            field = 'status' if model is self.item else 'is_published'
            original = getattr(model, field)
            setattr(model, field, 'DRAFT' if model is self.item else False)
            model.save()
            self.assertEqual(self.client.get(self.path).status_code, 404)
            setattr(model, field, original)
            model.save()

    def test_read_patch_and_revision_conflict(self):
        response = self.client.patch(self.path, {'revision': 1, 'content': lecture()}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['revision'], 2)
        self.assertEqual(self.client.get(self.path).data['content'], lecture())
        self.assertEqual(self.client.patch(self.path, {'revision': 1, 'parameters': self.parameters}, format='json').status_code, 409)
        self.assertFalse(UserLearningItemProgress.objects.exists())

    def test_invalid_content_or_unknown_write_fields_are_rejected(self):
        for change in ({'content': {}}, {'content': {'title': 'Incomplete', 'sections': []}}, {'owner': self.other.pk}, {'content': None}):
            self.assertEqual(self.client.patch(self.path, {'revision': 1, **change}, format='json').status_code, 400)
        self.document.refresh_from_db()
        self.assertEqual(self.document.revision, 1)

    def test_generate_enqueue_is_idempotent_and_hides_internal_data(self):
        data = self.operation_data()
        response = self.client.post(self.path + 'operations/', data, format='json')
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertEqual(self.client.post(self.path + 'operations/', data, format='json').data['id'], response.data['id'])
        status = self.client.get(f"/api/v1/workspace/operations/{response.data['id']}/")
        self.assertEqual(status.status_code, 200)
        self.assertFalse({'input_data', 'lease_token', 'lease_expires_at'} & set(status.data))
        self.assertNotIn('private-secret', json.dumps(status.data))
        self.assertEqual(WorkspaceMessage.objects.count(), 1)
        self.assertEqual(self.client.get(self.path + 'messages/').data['results'][0]['text'], data['prompt'])

    def test_changed_idempotency_key_payload_and_duplicate_active_job_conflict(self):
        data = self.operation_data()
        self.client.post(self.path + 'operations/', data, format='json')
        self.assertEqual(self.client.post(self.path + 'operations/', {**data, 'prompt': 'Другой'}, format='json').status_code, 409)
        self.assertEqual(self.client.post(self.path + 'operations/', self.operation_data(), format='json').status_code, 409)

    def test_outdated_revision_and_section_edit_are_rejected(self):
        self.assertEqual(self.client.post(self.path + 'operations/', self.operation_data(revision=2), format='json').status_code, 409)
        self.assertEqual(self.client.post(self.path + 'operations/', self.operation_data(target_section_id='goal'), format='json').status_code, 400)
        self.assertEqual(self.client.post(self.path + 'operations/', self.operation_data(kind='edit_section'), format='json').status_code, 400)

    @override_settings(AI_MODEL='')
    def test_missing_ai_configuration_returns_503_without_messages(self):
        self.assertEqual(self.client.post(self.path + 'operations/', self.operation_data(), format='json').status_code, 503)
        self.assertFalse(WorkspaceOperation.objects.exists())
        self.assertFalse(WorkspaceMessage.objects.exists())

    def test_submission_uses_saved_snapshot_and_existing_self_assessment(self):
        self.client.patch(self.path, {'revision': 1, 'content': lecture()}, format='json')
        data = {'submission_id': self.document.submission_id, 'submission_data': {
            'checks': [True], 'document_id': self.document.pk, 'document_revision': 2}}
        response = self.client.post(self.item_path, data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['result']['grading_source'], 'SELF_ASSESSMENT')
        self.assertEqual(response.data['submission']['submission_data']['document']['content'], lecture())
        self.assertEqual(self.client.post(self.item_path, data, format='json').status_code, 200)
        self.assertEqual(PracticeResult.objects.count(), 1)
        self.assertEqual(self.client.patch(self.path, {'revision': 2, 'content': lecture()}, format='json').status_code, 409)
        self.assertEqual(self.client.post(self.path + 'operations/', self.operation_data(revision=2), format='json').status_code, 409)

    def test_submission_cannot_reference_other_attempt_or_empty_document(self):
        data = {'submission_id': self.document.submission_id, 'submission_data': {
            'checks': [True], 'document_id': self.document.pk, 'document_revision': 1}}
        self.assertEqual(self.client.post(self.item_path, data, format='json').status_code, 400)
        other_document, _ = create_document(self.other, self.item, self.parameters)
        data['submission_data']['document_id'] = other_document.pk
        self.assertEqual(self.client.post(self.item_path, data, format='json').status_code, 404)
        self.assertFalse(PracticeResult.objects.exists())

    def test_submission_waits_for_pending_operation(self):
        self.client.patch(self.path, {'revision': 1, 'content': lecture()}, format='json')
        self.document.refresh_from_db()
        self.queue()
        data = {'submission_id': self.document.submission_id, 'submission_data': {
            'checks': [True], 'document_id': self.document.pk, 'document_revision': 2}}
        self.assertEqual(self.client.post(self.item_path, data, format='json').status_code, 409)

    def test_workspace_keeps_pinned_assignment_version(self):
        old_version = self.document.submission.practice_version_id
        save_draft(self.item, self.user, {**self.draft, 'title': 'New version'})
        publish_draft(self.item)
        response = self.client.post(self.item_path + 'workspace/', {'parameters': self.parameters}, format='json')
        self.assertEqual(response.status_code, 200)
        self.document.submission.refresh_from_db()
        self.assertEqual(self.document.submission.practice_version_id, old_version)

    def test_legacy_checklist_opens_as_a_chat_without_losing_its_version(self):
        from .testing import configure_checklist
        item = LearningItem.objects.create(title='Checklist', module=self.module, position=1, type='PRACTICE')
        configure_checklist(item, ['Checked'], self.user)
        response = self.client.post(f'/api/v1/items/{item.short_id}/practice/workspace/', {'parameters': self.parameters}, format='json')
        self.assertEqual(response.status_code, 201)
        submission = PracticeSubmission.objects.get(item=item)
        self.assertEqual(submission.practice_version.practice_definition.practice_type, 'checklist_practice')

    def test_chat_creation_needs_only_language_and_restores_document_in_runtime_state(self):
        self.client.force_login(self.other)
        response = self.client.post(self.item_path + 'workspace/', {'language': 'kk'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['parameters']['language'], 'kk')
        state = self.client.get(self.item_path)
        self.assertEqual(state.data['submission']['document_id'], response.data['id'])

    def test_workshop_publishes_without_criteria_and_can_replace_legacy_assignment(self):
        from .testing import configure_checklist
        item = LearningItem.objects.create(title='Legacy', module=self.module, position=1, type='PRACTICE')
        configure_checklist(item, ['Old criterion'], self.user)
        old_version = item.practice_version
        save_draft(item, self.user, {**self.draft, 'config': {}})
        publish_draft(item)
        item.refresh_from_db()
        self.assertEqual(item.practice.practice_type, 'lecture_workshop')
        self.assertEqual(item.practice_version.config, {})
        self.assertEqual(item.practice_version.criteria.count(), 0)
        old_version.refresh_from_db()
        self.assertEqual(old_version.practice_definition.practice_type, 'checklist_practice')
        self.assertEqual(old_version.criteria.count(), 1)

    def test_done_completes_without_a_lecture_and_does_not_record_self_assessment(self):
        data = {'submission_id': self.document.submission_id, 'submission_data': {'completed': True}}
        response = self.client.post(self.item_path, data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['progress_percent'], 100)
        self.assertEqual(response.data['result']['grading_source'], 'COMPLETION')
        self.assertIsNone(response.data['result']['self_assessment_score'])
        self.assertEqual(response.data['submission']['criteria_snapshot'], [])
        self.assertEqual(self.client.post(self.item_path, data, format='json').status_code, 200)
        self.assertEqual(PracticeResult.objects.count(), 1)

    def test_done_without_opening_a_chat_is_allowed(self):
        from .services import start_submission
        self.client.force_login(self.other)
        submission = start_submission(self.other, self.item)
        response = self.client.post(self.item_path, {'submission_id': submission.pk,
            'submission_data': {'completed': True}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_completed'])
        self.assertFalse(WorkspaceDocument.objects.filter(submission=submission).exists())

    def test_done_snapshots_generated_lecture_and_waits_for_queued_work(self):
        self.client.patch(self.path, {'revision': 1, 'content': lecture()}, format='json')
        self.document.refresh_from_db()
        queued = self.queue()
        data = {'submission_id': self.document.submission_id, 'submission_data': {'completed': True}}
        self.assertEqual(self.client.post(self.item_path, data, format='json').status_code, 409)
        queued.status = 'FAILED'
        queued.save()
        response = self.client.post(self.item_path, data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['submission']['submission_data']['document']['content'], lecture())

    def test_done_rejects_false_values_unknown_fields_and_foreign_documents(self):
        for payload in ({'completed': False}, {'completed': 1}, {'completed': True, 'score': 1}):
            self.assertEqual(self.client.post(self.item_path, {'submission_id': self.document.submission_id,
                'submission_data': payload}, format='json').status_code, 400)
        other_document, _ = create_document(self.other, self.item, self.parameters)
        response = self.client.post(self.item_path, {'submission_id': self.document.submission_id,
            'submission_data': {'completed': True, 'document_id': other_document.pk, 'document_revision': 1}}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PracticeResult.objects.exists())

    def test_followup_uses_previous_conversation_and_current_lecture(self):
        self.queue()
        with patch('apps.activities.workspace.worker.run_ai', return_value=ai_response()):
            execute_operation(claim_next_operation())
        self.document.refresh_from_db()
        followup = self.queue()
        messages = followup.input_data['messages']
        self.assertGreater(len(messages), 2)
        self.assertTrue(any(row['role'] == 'assistant' and lecture()['title'] in row['content'] for row in messages))
        history = self.client.get(self.path + 'messages/').data['results']
        self.assertEqual(history[-2]['content'], lecture())


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   AI_BASE_URL='https://provider.test/v1', AI_MODEL='configured-model', AI_API_KEY='private-secret')
class WorkspaceWorkerTests(WorkspaceFixture, TestCase):
    def test_generation_applies_once_and_records_telemetry(self):
        self.queue()
        claimed = claim_next_operation()
        self.assertIsNone(claim_next_operation())
        with patch('apps.activities.workspace.worker.run_ai', return_value=ai_response()):
            self.assertTrue(execute_operation(claimed))
        self.assertFalse(finish_operation(claimed, ai_response()))
        self.document.refresh_from_db()
        claimed.refresh_from_db()
        self.assertEqual(self.document.content, lecture())
        self.assertEqual(self.document.revision, 2)
        self.assertEqual(claimed.status, 'SUCCEEDED')
        self.assertEqual(claimed.model, 'actual-model')
        self.assertEqual(claimed.total_tokens, 300)
        self.assertEqual(claimed.cost, Decimal('0.001'))
        self.assertIsNotNone(claimed.duration_ms)
        self.assertEqual(WorkspaceMessage.objects.filter(role='assistant').count(), 1)

    def test_improve_prompt_never_changes_document_or_generates_lecture(self):
        operation = self.queue('improve_prompt')
        self.assertIn('Do not generate a lecture', operation.input_data['messages'][0]['content'])
        claimed = claim_next_operation()
        with patch('apps.activities.workspace.worker.run_ai', return_value=ai_response({'improved_prompt': 'Улучшенный запрос'})):
            execute_operation(claimed)
        self.document.refresh_from_db()
        claimed.refresh_from_db()
        self.assertEqual(self.document.revision, 1)
        self.assertEqual(self.document.content, {})
        self.assertEqual(claimed.result, {'improved_prompt': 'Улучшенный запрос'})

    def test_newer_manual_revision_is_never_overwritten(self):
        self.queue()
        claimed = claim_next_operation()
        self.client.patch(self.path, {'revision': 1, 'content': lecture()}, format='json')
        self.assertTrue(finish_operation(claimed, ai_response()))
        claimed.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual(claimed.error_code, 'revision_conflict')
        self.assertEqual(claimed.total_tokens, 300)
        self.assertEqual(self.document.revision, 2)
        self.assertFalse(WorkspaceMessage.objects.filter(role='assistant').exists())

    def test_outdated_queued_operation_does_not_call_provider(self):
        self.queue()
        self.client.patch(self.path, {'revision': 1, 'parameters': self.parameters}, format='json')
        claimed = claim_next_operation()
        with patch('apps.activities.workspace.worker.run_ai') as provider:
            execute_operation(claimed)
        provider.assert_not_called()
        claimed.refresh_from_db()
        self.assertEqual(claimed.error_code, 'revision_conflict')

    def test_revoked_access_is_checked_before_and_after_ai(self):
        for before in (False, True):
            self.user.course_access_mode = 'TRACK_DEFAULT'
            self.user.save()
            self.queue()
            claimed = claim_next_operation()
            self.user.course_access_mode = 'CUSTOM'
            self.user.save()
            if before:
                with patch('apps.activities.workspace.worker.run_ai') as provider:
                    execute_operation(claimed)
                provider.assert_not_called()
            else:
                finish_operation(claimed, ai_response())
            claimed.refresh_from_db()
            self.assertEqual(claimed.error_code, 'access_revoked')
        self.document.refresh_from_db()
        self.assertEqual(self.document.content, {})

    def test_invalid_generation_and_improvement_fail_without_content_changes(self):
        for kind, payload in [('generate', {'title': 'Missing sections'}), ('improve_prompt', lecture())]:
            self.queue(kind)
            claimed = claim_next_operation()
            with patch('apps.activities.workspace.worker.run_ai', return_value=ai_response(payload)):
                execute_operation(claimed)
            claimed.refresh_from_db()
            self.assertEqual(claimed.error_code, 'ai_invalid_content')
            self.assertEqual(claimed.total_tokens, 300)
        self.document.refresh_from_db()
        self.assertEqual(self.document.revision, 1)

    def test_provider_timeout_and_unexpected_errors_are_safe(self):
        for error, expected in [(AIError('ai_timeout'), 'ai_timeout'), (RuntimeError('private-secret provider body'), 'worker_error')]:
            self.queue()
            claimed = claim_next_operation()
            with patch('apps.activities.workspace.worker.run_ai', side_effect=error):
                execute_operation(claimed)
            claimed.refresh_from_db()
            self.assertEqual(claimed.error_code, expected)
            self.assertNotIn('private-secret', json.dumps(claimed.result))

    def test_http_diagnostics_are_logged_without_changing_public_error(self):
        self.queue()
        claimed = claim_next_operation()
        error = AIError('ai_http_error', {'http_status': 401, 'provider_code': 'invalid_api_key',
                                       'request_id': 'req_test', 'message': 'private-secret'})
        with patch('apps.activities.workspace.worker.run_ai', side_effect=error):
            with self.assertLogs('apps.activities.workspace.worker', level='WARNING') as captured:
                execute_operation(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.error_code, 'ai_http_error')
        self.assertEqual(claimed.result, {})
        log = ' '.join(captured.output)
        self.assertIn(f'Workspace operation {claimed.pk}', log)
        self.assertIn('"http_status": 401', log)
        self.assertIn('req_test', log)
        self.assertNotIn('private-secret', log)

    def test_expired_lease_is_reclaimed_and_old_worker_is_fenced(self):
        self.queue()
        old = claim_next_operation()
        WorkspaceOperation.objects.filter(pk=old.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(recover_expired_operations(), 1)
        new = claim_next_operation()
        self.assertNotEqual(old.lease_token, new.lease_token)
        self.assertEqual(new.attempts, 2)
        self.assertFalse(finish_operation(old, ai_response()))
        self.assertTrue(finish_operation(new, ai_response()))
        self.document.refresh_from_db()
        self.assertEqual(self.document.revision, 2)

    def test_expired_lease_cannot_apply_even_before_recovery(self):
        self.queue()
        claimed = claim_next_operation()
        WorkspaceOperation.objects.filter(pk=claimed.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        finish_operation(claimed, ai_response())
        claimed.refresh_from_db()
        self.assertEqual(claimed.error_code, 'operation_timeout')
        self.document.refresh_from_db()
        self.assertEqual(self.document.revision, 1)

    def test_recovery_stops_after_three_crashed_attempts(self):
        self.queue()
        for attempt in range(3):
            claimed = claim_next_operation()
            self.assertEqual(claimed.attempts, attempt + 1)
            WorkspaceOperation.objects.filter(pk=claimed.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
            recover_expired_operations()
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, 'FAILED')
        self.assertEqual(claimed.error_code, 'operation_timeout')
        self.assertIsNone(claim_next_operation())

    def test_closed_submission_drops_result(self):
        self.queue()
        claimed = claim_next_operation()
        PracticeSubmission.objects.filter(pk=self.document.submission_id).update(status='SUBMITTED')
        finish_operation(claimed, ai_response())
        claimed.refresh_from_db()
        self.assertEqual(claimed.error_code, 'submission_closed')


class WorkspaceSchemaTests(SimpleTestCase):
    def test_safe_tiptap_roundtrip_including_lists_links_and_code(self):
        content = lecture()
        content['sections'][0]['content']['content'] = [
            {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': 'Цель'}]},
            {'type': 'bulletList', 'content': [{'type': 'listItem', 'content': [{'type': 'paragraph', 'content': [
                {'type': 'text', 'text': 'Ссылка', 'marks': [{'type': 'link', 'attrs': {
                    'href': 'https://example.test', 'target': '_blank', 'rel': 'noopener noreferrer', 'class': None, 'title': None}}]}]}]}]},
            {'type': 'orderedList', 'attrs': {'start': 1, 'type': None}, 'content': [
                {'type': 'listItem', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Первый пункт'}]}]}]},
            {'type': 'codeBlock', 'attrs': {'language': None}, 'content': [{'type': 'text', 'text': 'print(1)'}]}]
        self.assertEqual(validate_lecture(content), content)

    def test_missing_duplicate_or_reordered_stable_ids_are_rejected(self):
        for ids in (SECTION_IDS[:-1], ('goal',) * 10, tuple(reversed(SECTION_IDS))):
            value = lecture()
            value['sections'] = [{**value['sections'][0], 'id': key} for key in ids]
            with self.assertRaises(ValidationError):
                validate_lecture(value)

    def test_unsafe_or_malformed_tiptap_is_rejected(self):
        for node in ({'type': 'image'}, {'type': []}, {'type': 'paragraph', 'attrs': {'onclick': 'bad'}},
                     {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'bad', 'marks': [
                         {'type': 'link', 'attrs': {'href': 'javascript:alert(1)'}}]}]},
                     {'type': 'bulletList', 'content': [{'type': 'paragraph'}]}):
            value = lecture()
            value['sections'][0]['content']['content'] = [node]
            with self.assertRaises(ValidationError):
                validate_lecture(value)


class WorkspaceAIClientTests(SimpleTestCase):
    def transport_response(self, envelope):
        response = Mock()
        response.read.return_value = json.dumps(envelope).encode('utf-8')
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_configurable_request_json_and_available_telemetry(self):
        envelope = {'model': 'provider-model', 'choices': [{'message': {'content': json.dumps(lecture())}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5, 'cost': '0.02', 'cost_currency': 'USD'}}
        with patch('apps.activities.workspace.ai.build_opener') as opener:
            opener.return_value.open.return_value = self.transport_response(envelope)
            result = call_api('https://provider.test/v1', 'secret', 'requested-model', [{'role': 'user', 'content': 'test'}], 10)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://provider.test/v1/chat/completions')
            self.assertEqual(json.loads(request.data)['model'], 'requested-model')
            self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
        self.assertEqual(result['payload'], lecture())
        self.assertEqual(result['model'], 'provider-model')
        self.assertEqual(result['total_tokens'], 5)
        self.assertEqual(result['cost'], '0.02000000')

    def test_optional_metadata_is_nullable(self):
        envelope = {'choices': [{'message': {'content': '{"improved_prompt":"better"}'}}], 'usage': {'prompt_tokens': True, 'cost': 'NaN'}}
        with patch('apps.activities.workspace.ai.build_opener') as opener:
            opener.return_value.open.return_value = self.transport_response(envelope)
            result = call_api('http://localhost:1234/v1', '', 'local-model', [], 10)
        self.assertIsNone(result['input_tokens'])
        self.assertIsNone(result['cost'])
        self.assertEqual(result['model'], 'local-model')

    def test_invalid_json_and_truncated_response_are_rejected(self):
        for choice in ({'message': {'content': 'not JSON'}}, {'message': {'content': '{}'}, 'finish_reason': 'length'}):
            with patch('apps.activities.workspace.ai.build_opener') as opener:
                opener.return_value.open.return_value = self.transport_response({'choices': [choice]})
                with self.assertRaises(AIError):
                    call_api('https://provider.test/v1', 'secret', 'model', [], 10)

    def test_incomplete_response_reports_reason_and_token_budget(self):
        for reason in ('length', 'content_filter'):
            with self.subTest(reason=reason):
                envelope = {'choices': [{'finish_reason': reason, 'message': {'content': 'private lecture'}}],
                            'usage': {'completion_tokens': 2048,
                                      'completion_tokens_details': {'reasoning_tokens': 2000}}}
                with patch('apps.activities.workspace.ai.build_opener') as opener:
                    opener.return_value.open.return_value = self.transport_response(envelope)
                    with self.assertRaises(AIError) as caught:
                        call_api('https://provider.test/v1', 'secret-key', 'model', [], 10, 2048)
                self.assertEqual(caught.exception.code, 'ai_incomplete_response')
                self.assertEqual(caught.exception.diagnostics, {'finish_reason': reason,
                    'max_completion_tokens': 2048, 'completion_tokens': 2048, 'reasoning_tokens': 2000})

    def test_incomplete_response_handles_missing_or_malformed_usage(self):
        for usage in (None, [], {'completion_tokens': True, 'completion_tokens_details': []},
                      {'completion_tokens': -1, 'completion_tokens_details': {'reasoning_tokens': 'secret-key'}}):
            with self.subTest(usage=usage):
                envelope = {'choices': [{'finish_reason': 'length'}], 'usage': usage}
                with patch('apps.activities.workspace.ai.build_opener') as opener:
                    opener.return_value.open.return_value = self.transport_response(envelope)
                    with self.assertRaises(AIError) as caught:
                        call_api('https://provider.test/v1', 'secret-key', 'model', [], 10)
                self.assertEqual(caught.exception.code, 'ai_incomplete_response')
                self.assertEqual(caught.exception.diagnostics,
                                 {'finish_reason': 'length', 'max_completion_tokens': 16000})

    @override_settings(AI_BASE_URL='https://provider.test/v1', AI_API_KEY='secret-key')
    def test_incomplete_diagnostics_survive_child_process_boundary(self):
        operation = Mock(model='model', timeout_seconds=2, input_data={'messages': []})
        for reason in ('length', 'secret-key', 'length\nFORGED LOG'):
            with self.subTest(reason=reason):
                diagnostics = {'finish_reason': reason, 'max_completion_tokens': 2048,
                               'completion_tokens': 2048, 'reasoning_tokens': True, 'content': 'private lecture'}
                output = {'error_code': 'ai_incomplete_response', 'diagnostics': diagnostics}
                with patch('apps.activities.workspace.ai.subprocess.run',
                           return_value=Mock(returncode=0, stdout=json.dumps(output))):
                    with self.assertRaises(AIError) as caught:
                        run_ai(operation)
                expected = {'max_completion_tokens': 2048, 'completion_tokens': 2048}
                if reason == 'length':
                    expected['finish_reason'] = reason
                self.assertEqual(caught.exception.diagnostics, expected)

    def test_http_errors_do_not_expose_provider_body_or_secret(self):
        with patch('apps.activities.workspace.ai.build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('secret-url', 429, 'secret-provider-body', {}, None)
            with self.assertRaises(AIError) as caught:
                call_api('https://provider.test/v1', 'secret-key', 'model', [], 10)
        self.assertEqual(str(caught.exception), 'ai_rate_limited')

    def test_http_diagnostics_keep_status_and_identifiers_only(self):
        for status in (400, 401, 429, 503):
            with self.subTest(status=status):
                body = BytesIO(json.dumps({'error': {'code': 'invalid_api_key', 'type': 'invalid_request_error',
                    'param': 'messages[0].content', 'message': 'secret-key private lecture'}}).encode())
                error = HTTPError('secret-url', status, 'secret-reason', {'x-request-id': 'req_test'}, body)
                with patch('apps.activities.workspace.ai.build_opener') as opener:
                    opener.return_value.open.side_effect = error
                    with self.assertRaises(AIError) as caught:
                        call_api('https://provider.test/v1', 'secret-key', 'model', [], 10)
                self.assertEqual(caught.exception.diagnostics, {'http_status': status,
                    'provider_code': 'invalid_api_key', 'provider_type': 'invalid_request_error',
                    'provider_param': 'messages[0].content', 'request_id': 'req_test'})
                self.assertEqual(str(caught.exception), 'ai_rate_limited' if status == 429 else 'ai_http_error')
                self.assertTrue(body.closed)

    def test_http_diagnostics_reject_secrets_and_log_injection(self):
        body = BytesIO(json.dumps({'error': {'code': 'prefix-secret-key', 'type': 'sk-other-secret',
                                          'param': 'messages\nFORGED LOG'}}).encode())
        error = HTTPError('secret-url', 400, 'secret-reason', {'x-request-id': 'r' * 129}, body)
        with patch('apps.activities.workspace.ai.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(AIError) as caught:
                call_api('https://provider.test/v1', 'secret-key', 'model', [], 10)
        self.assertEqual(caught.exception.diagnostics, {'http_status': 400})

    def test_malformed_or_oversized_http_errors_preserve_status(self):
        for raw in (b'<html>private-secret</html>', b'[]', b'{"error": []}', b'x' * 16_385):
            with self.subTest(raw_length=len(raw)):
                error = HTTPError('secret-url', 502, 'secret-reason', {'x-request-id': 'req_test'}, BytesIO(raw))
                with patch('apps.activities.workspace.ai.build_opener') as opener:
                    opener.return_value.open.side_effect = error
                    with self.assertRaises(AIError) as caught:
                        call_api('https://provider.test/v1', 'secret-key', 'model', [], 10)
                self.assertEqual(caught.exception.diagnostics, {'http_status': 502, 'request_id': 'req_test'})

    @override_settings(AI_BASE_URL='https://provider.test/v1', AI_API_KEY='secret-key')
    def test_child_process_diagnostics_are_validated_before_logging(self):
        operation = Mock(model='model', timeout_seconds=2, input_data={'messages': []})
        output = {'error_code': 'ai_http_error', 'diagnostics': {'http_status': 400, 'provider_code': 'unsupported_parameter',
                  'provider_type': 'secret-key', 'request_id': 'req_test', 'message': 'private lecture'}}
        with patch('apps.activities.workspace.ai.subprocess.run', return_value=Mock(returncode=0, stdout=json.dumps(output))):
            with self.assertRaises(AIError) as caught:
                run_ai(operation)
        self.assertEqual(caught.exception.diagnostics, {'http_status': 400, 'provider_code': 'unsupported_parameter',
                                                       'request_id': 'req_test'})

    @override_settings(AI_BASE_URL='https://provider.test/v1', AI_API_KEY='secret-key')
    def test_process_timeout_is_hard_and_redacted(self):
        operation = Mock(model='model', timeout_seconds=2, input_data={'messages': []})
        with patch('apps.activities.workspace.ai.subprocess.run', side_effect=subprocess.TimeoutExpired('secret-command', 2)):
            with self.assertRaises(AIError) as caught:
                run_ai(operation)
        self.assertEqual(str(caught.exception), 'ai_timeout')

    def test_real_child_process_roundtrip_against_local_mock_provider(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        requests = []

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                body = json.dumps({'choices': [{'message': {'content': json.dumps(lecture(), ensure_ascii=False)}}]}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with override_settings(AI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1', AI_API_KEY=''):
                operation = Mock(model='local-test-model', timeout_seconds=10,
                                 input_data={'messages': [{'role': 'user', 'content': 'Создай лекцию'}]})
                result = run_ai(operation)
            self.assertEqual(result['payload'], lecture())
            self.assertEqual(requests[0][0], '/v1/chat/completions')
            self.assertEqual(requests[0][1]['messages'][0]['content'], 'Создай лекцию')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   AI_BASE_URL='https://provider.test/v1', AI_MODEL='test-model')
class WorkspaceWorkerCommandTests(WorkspaceFixture, TransactionTestCase):
    def test_once_command_processes_an_operation(self):
        # The command manages connection lifetimes and must run outside TestCase's
        # wrapping atomic block, just as it does in a standalone worker process.
        operation = self.queue()
        output = StringIO()
        with patch('apps.activities.workspace.worker.run_ai', return_value=ai_response()):
            call_command('run_workspace_worker', once=True, stdout=output)
        operation.refresh_from_db()
        self.document.refresh_from_db()
        self.assertIn('SUCCEEDED', output.getvalue())
        self.assertEqual(operation.status, 'SUCCEEDED')
        self.assertEqual(self.document.content, lecture())


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   AI_BASE_URL='https://provider.test/v1', AI_MODEL='test-model')
class WorkspacePostgreSQLTests(WorkspaceFixture, TransactionTestCase):
    def test_skip_locked_claims_other_task_and_never_claims_twice(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires an isolated PostgreSQL test database')
        from concurrent.futures import ThreadPoolExecutor
        first = self.queue()
        other_document, _ = create_document(self.other, self.item, self.parameters)
        create_operation(self.other, other_document, {'request_id': uuid.uuid4(), 'revision': 1,
                         'kind': 'generate', 'prompt': 'Other'})

        def independent_claim():
            close_old_connections()
            try:
                operation = claim_next_operation()
                return operation.pk if operation else None
            finally:
                connection.close()

        with transaction.atomic(), ThreadPoolExecutor(max_workers=1) as pool:
            WorkspaceOperation.objects.select_for_update().get(pk=first.pk)
            claimed_id = pool.submit(independent_claim).result(timeout=5)
            self.assertNotEqual(claimed_id, first.pk)
            self.assertIsNone(pool.submit(independent_claim).result(timeout=5))
        self.assertEqual(claim_next_operation().pk, first.pk)
        self.assertIsNone(claim_next_operation())
