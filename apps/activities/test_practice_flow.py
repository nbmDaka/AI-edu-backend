import json
from django.test import TestCase, override_settings
from .models import PracticeResult
from .services import save_draft, publish_draft
from .test_workspace import WorkspaceFixture, lecture
from .workspace.services import create_document


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   AI_BASE_URL='https://provider.test/v1', AI_MODEL='test-model')
class PracticeFlowTests(WorkspaceFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.old_submission = self.document.submission
        self.old_submission.status = 'SUBMITTED'
        self.old_submission.save()
        self.instructions = json.dumps({'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
            {'type': 'text', 'text': 'Подготовьте лекцию об ИИ для преподавателей.'}]}]}, ensure_ascii=False)
        self.config = {'instructions': [self.instructions], 'criteria': [
            {'title': 'Проверена точность', 'description': 'Исправьте ошибки', 'is_required': True},
            {'title': 'Добавлены дополнительные примеры', 'description': '', 'is_required': False},
        ]}
        save_draft(self.item, self.user, {**self.draft, 'config': self.config})
        publish_draft(self.item)
        self.item.refresh_from_db()
        self.document, _ = create_document(self.user, self.item, self.parameters)
        self.document.content = lecture()
        self.document.save()
        self.path = f'/api/v1/workspace/documents/{self.document.pk}/'

    def finish(self, **changes):
        data = {'completed': True, 'checks': [True, False], 'document_id': self.document.pk,
                'document_revision': self.document.revision, **changes}
        return self.client.post(self.item_path, {'submission_id': self.document.submission_id,
                                               'submission_data': data}, format='json')

    def test_shared_task_metadata_and_pinned_versions(self):
        response = self.client.get(self.item_path)
        self.assertEqual(response.data['version']['config'], self.config)
        self.assertEqual(response.data['submission']['id'], self.document.submission_id)
        self.old_submission.refresh_from_db()
        self.assertNotIn('instructions', self.old_submission.practice_version.config)

    def test_completion_requires_self_check_and_keeps_attempt_open_on_error(self):
        response = self.client.post(self.item_path, {'submission_id': self.document.submission_id,
                                                    'submission_data': {'completed': True}}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.finish(checks=[False, True]).status_code, 400)
        self.document.submission.refresh_from_db()
        self.assertEqual(self.document.submission.status, 'IN_PROGRESS')
        self.assertFalse(PracticeResult.objects.filter(submission_id=self.document.submission_id).exists())

    def test_final_submission_snapshots_lecture_and_criteria_and_is_idempotent(self):
        response = self.finish()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['progress_percent'], 100)
        self.assertEqual(response.data['result']['grading_source'], 'SELF_ASSESSMENT')
        self.assertEqual(response.data['result']['self_assessment_score'], .5)
        self.assertEqual([row['checked'] for row in response.data['submission']['criteria_snapshot']], [True, False])
        self.assertEqual(response.data['submission']['submission_data']['document']['content'], lecture())
        self.assertEqual(self.finish().data, response.data)

    def test_work_must_exist_and_pending_generation_blocks_final_submission(self):
        self.document.content = {}
        self.document.save()
        self.assertEqual(self.finish().status_code, 400)
        self.document.content = lecture()
        self.document.save()
        self.queue()
        self.assertEqual(self.finish().status_code, 409)
        self.assertFalse(PracticeResult.objects.filter(submission_id=self.document.submission_id).exists())

    def test_assignment_instructions_are_passed_to_the_lecture_assistant(self):
        operation = self.queue('chat')
        self.assertEqual(operation.input_data['assignment']['instructions'], [self.instructions])
        context = json.loads(operation.input_data['messages'][1]['content'])
        self.assertEqual(context['assignment']['criteria'], self.config['criteria'])

    def test_forged_checks_and_stale_revision_cannot_finish_the_assignment(self):
        for checks in ([1, False], [True], [True, False, True]):
            with self.subTest(checks=checks):
                self.assertEqual(self.finish(checks=checks).status_code, 400)
        self.assertEqual(self.finish(document_revision=self.document.revision + 1).status_code, 409)
