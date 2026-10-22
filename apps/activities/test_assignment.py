import json
from copy import deepcopy

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.education.models import Course, Module, LearningItem, LearningTrack
from apps.tutor.context import practice_context


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class GuidedAssignmentTests(TestCase):
    def setUp(self):
        track = LearningTrack.objects.create(title='Teachers', is_published=True)
        course = Course.objects.create(title='Teaching', learning_track=track, is_published=True)
        module = Module.objects.create(title='Module 1', course=course, is_published=True)
        self.item = LearningItem.objects.create(title='Task', module=module, type='PRACTICE')
        self.admin = User.objects.create_user('admin@assignment.test', 'Password123!', role='ADMIN')
        self.student = User.objects.create_user('student@assignment.test', 'Password123!', learning_track=track)
        self.other = User.objects.create_user('other@assignment.test', 'Password123!', learning_track=track)
        self.client = APIClient()
        self.client.force_login(self.admin)
        self.path = f'/api/v1/items/{self.item.short_id}/practice/'
        instructions = {'type': 'doc', 'content': [{'type': 'table', 'content': [
            {'type': 'tableRow', 'content': [{'type': 'tableCell', 'attrs': {'backgroundColor': '#123456'},
                'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Original source task'}]}]}]}]}]}
        self.payload = {'practice_type': 'guided_assignment', 'title': 'Source-based task',
            'description': 'Prepare your own materials', 'config': {'instructions': [json.dumps(instructions)],
            'criteria': [{'title': 'Verify sources', 'description': 'Record corrections', 'is_required': True}],
            'source_url': 'https://docs.google.com/document/d/source/edit'}}
        self.answer = {'report': 'I checked the source facts and corrected the generated teaching materials.',
                       'artifact_url': 'https://example.org/result', 'checks': [True]}

    def publish(self):
        response = self.client.put(self.path + 'draft/', self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(self.path + 'draft/')
        self.assertEqual(response.status_code, 200, response.data)
        self.item.refresh_from_db()
        self.client.force_login(self.student)
        return self.client.post(self.path + 'start/').data['submission']['id']

    def test_real_instructions_and_tables_survive_publication(self):
        self.publish()
        state = self.client.get(self.path).data
        self.assertEqual(state['version']['config'], self.payload['config'])
        self.assertIn('Original source task', practice_context(self.student, self.item)[2])

    def test_report_submission_is_self_assessment_not_ai_or_teacher_grade(self):
        submission = self.publish()
        data = {'submission_id': submission, 'submission_data': self.answer}
        result = self.client.post(self.path, data, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['is_completed'])
        self.assertEqual(result.data['result']['grading_source'], 'SELF_ASSESSMENT')
        self.assertIsNone(result.data['result']['automatic_score'])
        self.assertIsNone(result.data['result']['teacher_score'])
        self.assertEqual(self.client.post(self.path, data, format='json').data, result.data)
        self.assertIn(self.answer['report'], practice_context(self.student, self.item)[2])

    def test_missing_report_and_unsafe_links_are_rejected(self):
        submission = self.publish()
        for change in ({'report': ''}, {'artifact_url': 'javascript:alert(1)'},
                       {'artifact_url': 'https://user:pass@example.org'}, {'checks': [1]}, {'checks': []}):
            with self.subTest(change=change):
                response = self.client.post(self.path, {'submission_id': submission,
                    'submission_data': {**self.answer, **change}}, format='json')
                self.assertEqual(response.status_code, 400, response.data)

    def test_incomplete_self_assessment_does_not_complete_material(self):
        submission = self.publish()
        response = self.client.post(self.path, {'submission_id': submission,
            'submission_data': {**self.answer, 'checks': [False]}}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.path).data['submission']['status'], 'IN_PROGRESS')
        self.assertFalse(self.client.get(self.path).data['is_completed'])

    def test_report_is_private_to_its_learner(self):
        submission = self.publish()
        self.client.force_login(self.other)
        response = self.client.post(self.path, {'submission_id': submission, 'submission_data': self.answer}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.client.get(self.path).data['submission'])
        self.assertNotIn(self.answer['report'], practice_context(self.other, self.item)[2])

    def test_invalid_instruction_documents_and_sources_fail_validation(self):
        for change in ({'instructions': ['<script>bad</script>']}, {'instructions': [1]},
                       {'instructions': [json.dumps({'type': 'script', 'content': []})]},
                       {'source_url': 'https://unrelated.example.org/private'},
                       {'source_url': 'https://['}):
            with self.subTest(change=change):
                payload = deepcopy(self.payload)
                payload['config'].update(change)
                response = self.client.put(self.path + 'draft/', payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)

    def test_other_trajectory_cannot_read_or_submit_assignment(self):
        self.publish()
        self.student.learning_track = LearningTrack.objects.create(title='Other', is_published=True)
        self.student.save()
        self.assertEqual(self.client.get(self.path).status_code, 404)
