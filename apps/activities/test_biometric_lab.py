import json

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.education.models import Course, Module, LearningItem, LearningTrack

from .biometric_lab import valid_lab_url


class LabUrlTests(SimpleTestCase):
    def test_only_https_or_local_http_without_credentials(self):
        for good in ['https://lab.example.org/', 'https://lab.example.org:8443/?lang=kk', 'http://localhost:8000/', 'http://127.0.0.1:8000/', 'http://127.5.6.7/']:
            self.assertTrue(valid_lab_url(good), good)
        for bad in ['', None, 5, 'http://lab.example.org/', 'ftp://lab.example.org/', 'javascript:alert(1)', 'https://user:pw@lab.example.org/',
                    'https:///no-host', 'https://lab.example.org/#frag', 'https://' + 'a' * 2000, 'localhost:8000']:
            self.assertFalse(valid_lab_url(bad), bad)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BiometricLabPracticeTests(TestCase):
    def setUp(self):
        track = LearningTrack.objects.create(title='Biometrics', is_published=True)
        course = Course.objects.create(title='Biometrics course', learning_track=track, is_published=True)
        module = Module.objects.create(title='Module 1', course=course, is_published=True)
        self.item = LearningItem.objects.create(title='Lab', module=module, type='PRACTICE')
        self.admin = User.objects.create_user('admin@biolab.test', 'Password123!', role='ADMIN')
        self.student = User.objects.create_user('student@biolab.test', 'Password123!', learning_track=track)
        self.client = APIClient()
        self.client.force_login(self.admin)
        self.path = f'/api/v1/items/{self.item.short_id}/practice/'
        instructions = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Compare two fingerprints'}]}]}
        self.payload = {'practice_type': 'biometric_lab', 'title': 'Biometric lab', 'description': 'Work in the lab',
                        'config': {'instructions': [json.dumps(instructions)], 'lab_url': 'https://lab.example.org/', 'lab_language': 'kk',
                                   'criteria': [{'title': 'Compared two samples', 'description': '', 'is_required': True}]}}
        self.answer = {'checks': [True], 'lab_opened': True, 'note': 'FAR and FRR checked'}

    def draft(self, payload=None):
        return self.client.put(self.path + 'draft/', payload or self.payload, format='json')

    def start(self):
        self.assertEqual(self.draft().status_code, 200)
        self.assertEqual(self.client.post(self.path + 'draft/').status_code, 200)
        self.client.force_login(self.student)
        return self.client.post(self.path + 'start/').data['submission']['id']

    def test_listed_in_catalog_with_schema(self):
        rows = {row['type']: row for row in self.client.get('/api/v1/practice-types/').data}
        manifest = rows['biometric_lab']
        self.assertEqual(manifest['student_runtime'], 'biometric_lab')
        self.assertIn('lab_url', manifest['config_schema']['required'])
        self.assertIn('lab_language', manifest['config_schema']['properties'])

    def test_config_requires_safe_lab_url_and_language(self):
        for key, value in [('lab_url', 'http://lab.example.org/'), ('lab_url', ''), ('lab_language', 'de')]:
            payload = json.loads(json.dumps(self.payload))
            payload['config'][key] = value
            self.assertEqual(self.draft(payload).status_code, 400, (key, value))
        payload = json.loads(json.dumps(self.payload))
        del payload['config']['lab_url']
        self.assertEqual(self.draft(payload).status_code, 400)
        payload = json.loads(json.dumps(self.payload))
        payload['config']['unexpected'] = 1
        self.assertEqual(self.draft(payload).status_code, 400)
        self.assertEqual(self.draft().status_code, 200)

    def test_submission_is_self_assessment(self):
        submission = self.start()
        data = {'submission_id': submission, 'submission_data': self.answer}
        result = self.client.post(self.path, data, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['is_completed'])
        self.assertEqual(result.data['result']['grading_source'], 'SELF_ASSESSMENT')
        self.assertIsNone(result.data['result']['automatic_score'])
        self.assertEqual(self.client.post(self.path, data, format='json').data, result.data)  # повторная отправка идемпотентна

    def test_rejects_incomplete_or_foreign_data(self):
        submission = self.start()
        bad = [{**self.answer, 'lab_opened': False}, {**self.answer, 'checks': [False]}, {**self.answer, 'note': 'x' * 2001},
               {**self.answer, 'extra': 1}, {'checks': [True]}, {**self.answer, 'checks': [True, True]}, {**self.answer, 'note': 5}]
        for answer in bad:
            response = self.client.post(self.path, {'submission_id': submission, 'submission_data': answer}, format='json')
            self.assertEqual(response.status_code, 400, answer)
