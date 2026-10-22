from copy import deepcopy

from django.core.exceptions import ValidationError as ModelValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.education.models import LearningTrack, Course, Module, LearningItem, PracticeSubmission, UserLearningItemProgress
from apps.education.adaptive import practice_indicator
from .models import PracticeVersion, PracticeCriterion, PracticeResult
from .testing import submit_checklist


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PracticeEngineTests(TestCase):
    def setUp(self):
        self.track = LearningTrack.objects.create(title='Track', is_published=True)
        self.course = Course.objects.create(title='Course', learning_track=self.track, is_published=True)
        self.module = Module.objects.create(title='Module', course=self.course, is_published=True)
        self.item = LearningItem.objects.create(title='Practice', module=self.module, type='PRACTICE')
        self.admin = User.objects.create_user('admin@practice.test', 'Password123!', role='ADMIN')
        self.student = User.objects.create_user('student@practice.test', 'Password123!', learning_track=self.track)
        self.other = User.objects.create_user('other@practice.test', 'Password123!', learning_track=self.track)
        self.client, self.admin_client = APIClient(), APIClient()
        self.client.force_login(self.student)
        self.admin_client.force_login(self.admin)
        self.path = f'/api/v1/items/{self.item.short_id}/practice/'
        self.config = {'criteria': [{'title': str(i), 'description': 'Criterion details', 'is_required': True} for i in range(4)]}
        self.draft = {'practice_type': 'checklist_practice', 'plugin_version': '1.0.0', 'title': 'Task v1', 'description': 'Instructions v1', 'config': self.config}

    def publish(self, payload=None):
        response = self.admin_client.put(self.path + 'draft/', payload or self.draft, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.admin_client.post(self.path + 'draft/')
        self.assertEqual(response.status_code, 200, response.data)
        self.item.refresh_from_db()
        return response.data

    def start(self):
        response = self.client.post(self.path + 'start/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['submission']['id']

    def test_catalog_and_unconfigured_creation(self):
        response = self.client.get('/api/v1/practice-types/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['type'] for row in response.data], [
            'lecture_workshop', 'practical_workshop', 'pedagogical_workshop', 'syllabus_workshop', 'guided_assignment'])
        self.assertEqual(response.data[0]['grading'], 'self_assessment')
        self.assertIn('criteria', response.data[0]['capabilities'])
        self.assertIn('config_schema', response.data[0])
        self.assertEqual(self.admin_client.get(self.path + 'draft/').data['versions'], [])
        self.assertEqual(self.admin_client.patch(f'/api/v1/items/{self.item.short_id}/', {'status': 'PUBLISHED'}, format='json').status_code, 400)

    def test_draft_never_changes_student_visible_content(self):
        old = self.publish()
        self.draft['title'] = 'Edited task'
        self.draft['config']['criteria'][0]['title'] = 'Edited criterion'
        self.assertEqual(self.admin_client.put(self.path + 'draft/', self.draft, format='json').status_code, 200)
        state = self.client.get(self.path).data
        self.assertEqual(state['version']['id'], old['id'])
        self.assertEqual(state['version']['title'], 'Task v1')
        self.assertEqual(state['version']['config']['criteria'][0]['title'], '0')
        self.assertFalse(PracticeSubmission.objects.exists())
        self.assertFalse(UserLearningItemProgress.objects.exists())

    def test_version_pin_and_new_attempt_use_new_version(self):
        old = self.publish()
        attempt = self.start()
        changed = deepcopy(self.draft)
        changed['title'], changed['description'] = 'Task v2', 'Instructions v2'
        changed['config']['criteria'].append({'title': 'Extra', 'description': '', 'is_required': True})
        new = self.publish(changed)
        self.assertNotEqual(old['id'], new['id'])
        self.assertEqual(self.client.get(self.path).data['version']['title'], 'Task v1')
        submitted = self.client.post(self.path, {'submission_id': attempt, 'submission_data': {'checks': [True, True, True, False]}}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data['result']['psi_value'], .75)
        self.assertEqual(submitted.data['result']['psi_source'], 'SELF_ASSESSMENT')
        self.start()
        self.assertEqual(self.client.get(self.path).data['version']['id'], new['id'])
        historic = PracticeSubmission.objects.get(pk=attempt)
        self.assertEqual(len(historic.criteria_snapshot), 4)
        self.assertEqual(historic.practice_version_id, old['id'])
        self.assertEqual(historic.result.psi_value, .75)

    def test_partial_score_and_full_completion_are_server_authoritative(self):
        self.publish()
        response = submit_checklist(self.client, self.item, [True, True, True, False])
        self.assertEqual(response.data['progress_percent'], 75)
        self.assertFalse(response.data['is_completed'])
        self.assertEqual(practice_indicator(self.student, self.module), .75)
        response = submit_checklist(self.client, self.item, [True] * 4)
        self.assertEqual(response.data['progress_percent'], 100)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(PracticeResult.objects.count(), 2)

    def test_optional_criteria_affect_psi_but_not_completion(self):
        self.draft['config']['criteria'][-1]['is_required'] = False
        self.publish()
        response = submit_checklist(self.client, self.item, [True, True, True, False])
        self.assertEqual(response.data['result']['psi_value'], .75)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['progress_percent'], 100)

    def test_submission_rejects_forged_scores_invalid_checks_and_unknown_fields(self):
        self.publish()
        attempt = self.start()
        for data in [{'psi': 1}, {'checks': [True] * 4, 'psi': 1}, {'checks': [1, 0, 1, 0]},
                     {'checks': ['true'] * 4}, {'checks': [True]}, {'checks': [True] * 5}, {'checks': None}]:
            response = self.client.post(self.path, {'submission_id': attempt, 'submission_data': data}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.client.post(self.path, {'submission_id': attempt, 'submission_data': {'checks': [True] * 4}, 'psi': 1}, format='json').status_code, 400)
        self.assertFalse(PracticeResult.objects.exists())
        self.assertFalse(UserLearningItemProgress.objects.exists())

    def test_invalid_configs_and_unimplemented_types_do_not_create_versions(self):
        for config in [{}, {'criteria': []}, {'criteria': ['a']}, {'criteria': [{'title': ' ', 'description': '', 'is_required': True}]},
                       {'criteria': [{'title': 'a', 'description': '', 'is_required': 1}]}]:
            payload = {**self.draft, 'config': config}
            self.assertEqual(self.admin_client.put(self.path + 'draft/', payload, format='json').status_code, 400)
        self.assertEqual(self.admin_client.put(self.path + 'draft/', {**self.draft, 'practice_type': 'python_ide'}, format='json').status_code, 400)
        self.assertFalse(PracticeVersion.objects.exists())

    def test_published_models_and_criteria_cannot_be_mutated(self):
        version = PracticeVersion.objects.get(pk=self.publish()['id'])
        criterion = version.criteria.first()
        for action in [lambda: version.save(), lambda: version.delete(), lambda: PracticeVersion.objects.filter(pk=version.pk).update(config={}),
                       lambda: criterion.save(), lambda: criterion.delete(), lambda: version.criteria.all().delete(),
                       lambda: version.criteria.all().update(title='Changed'),
                       lambda: PracticeCriterion.objects.create(practice_version=version, position=9, title='Extra')]:
            with self.assertRaises(ModelValidationError):
                action()

    def test_start_and_submission_retries_are_idempotent(self):
        self.publish()
        attempt = self.start()
        self.assertEqual(self.start(), attempt)
        payload = {'submission_id': attempt, 'submission_data': {'checks': [True] * 4}}
        for _ in range(2):
            self.assertEqual(self.client.post(self.path, payload, format='json').status_code, 200)
        self.assertEqual(PracticeSubmission.objects.count(), 1)
        self.assertEqual(PracticeResult.objects.count(), 1)
        payload['submission_data']['checks'][0] = False
        self.assertEqual(self.client.post(self.path, payload, format='json').status_code, 400)

    def test_attempt_ownership_and_admin_only_authoring(self):
        self.publish()
        attempt = self.start()
        for method in ('get', 'post', 'put'):
            self.assertEqual(getattr(self.client, method)(self.path + 'draft/', self.draft if method == 'put' else None, format='json').status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.path, {'submission_id': attempt, 'submission_data': {'checks': [True] * 4}}, format='json').status_code, 404)
        self.assertEqual(self.client.get(self.path + 'history/').data['count'], 0)
        self.assertIsNone(self.client.get(self.path).data['submission'])

    def test_course_access_and_adaptive_lock_apply_to_runtime_start_and_history(self):
        self.publish()
        self.student.course_access_mode = 'CUSTOM'
        self.student.save()
        for suffix in ['', 'history/']:
            self.assertEqual(self.client.get(self.path + suffix).status_code, 404)
        self.assertEqual(self.client.post(self.path + 'start/').status_code, 404)
        self.student.course_access_mode = 'TRACK_DEFAULT'
        self.student.save()
        self.course.adaptive_learning_enabled = True
        self.course.save()
        Module.objects.create(title='Prior module', course=self.course, position=0, is_published=True)
        self.module.position = 1
        self.module.save()
        self.assertEqual(self.client.get(self.path).status_code, 403)
        self.assertEqual(self.client.post(self.path + 'start/').status_code, 403)
        self.assertEqual(self.client.get(self.path + 'history/').status_code, 403)

    def test_deletion_with_history_archives_content_and_keeps_results(self):
        self.publish()
        submit_checklist(self.client, self.item, [True] * 4)
        self.assertEqual(self.admin_client.delete(f'/api/v1/items/{self.item.short_id}/').status_code, 204)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.admin_client.delete(f'/api/v1/modules/{self.module.short_id}/').status_code, 204)
        self.assertEqual(PracticeResult.objects.count(), 1)
