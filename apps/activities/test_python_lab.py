from copy import deepcopy

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.education.models import Course, Module, LearningItem, LearningTrack
from apps.mediafiles.models import MediaFile
from .registry import registry


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PythonLabTests(TestCase):
    def setUp(self):
        track = LearningTrack.objects.create(title='Teachers', is_published=True)
        course = Course.objects.create(title='Data mining', learning_track=track, is_published=True)
        module = Module.objects.create(title='Module 1', course=course, is_published=True)
        self.item = LearningItem.objects.create(title='Import data', module=module, type='PRACTICE')
        self.admin = User.objects.create_user('admin@lab.test', 'Password123!', role='ADMIN')
        self.student = User.objects.create_user('student@lab.test', 'Password123!', learning_track=track)
        self.client = APIClient()
        self.client.force_login(self.admin)
        self.path = f'/api/v1/items/{self.item.short_id}/practice/'
        self.config = {'packages': ['pandas'], 'datasets': [], 'steps': [
            {'title': 'Load', 'instructions': 'Read the CSV file', 'starter_code': 'import pandas as pd\n',
             'checks': [{'title': 'Rows', 'expression': 'df.shape[0]', 'expected': 1275, 'tolerance': 0, 'is_required': True},
                        {'title': 'Columns', 'expression': 'list(df.columns[:2])', 'expected': ['Company', 'Price'],
                         'tolerance': 0, 'is_required': True}]},
            {'title': 'Mean', 'instructions': 'Average price', 'starter_code': '',
             'checks': [{'title': 'Mean price', 'expression': 'round(df.Price.mean(), 2)', 'expected': 1134.97,
                         'tolerance': 0.01, 'is_required': False}]}]}
        self.answer = {'code': ['df = pd.read_csv("laptops.csv")', 'df.Price.mean()'],
                       'results': [{'value': 1275}, {'value': ['Company', 'Price']}, {'value': 1134.975}]}

    def publish(self, config=None):
        payload = {'practice_type': 'python_lab', 'title': 'Import data', 'description': '',
                   'config': config or self.config}
        response = self.client.put(self.path + 'draft/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.post(self.path + 'draft/').status_code, 200)
        self.client.force_login(self.student)
        return self.client.post(self.path + 'start/').data['submission']['id']

    def send(self, submission, answer):
        return self.client.post(self.path, {'submission_id': submission, 'submission_data': answer}, format='json')

    def test_manifest_is_listed_in_catalog(self):
        types = {row['type'] for row in self.client.get('/api/v1/practice-types/').data}
        self.assertIn('python_lab', types)

    def test_matching_results_are_auto_graded_and_complete_the_item(self):
        response = self.send(self.publish(), self.answer)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['result']['grading_source'], 'AUTO_GRADING')
        self.assertEqual(response.data['result']['automatic_score'], 1)
        self.assertEqual(len(response.data['submission']['criteria_snapshot']), 3)

    def test_optional_check_failure_gives_partial_score_but_completes(self):
        answer = deepcopy(self.answer)
        answer['results'][2] = {'value': 1000}
        response = self.send(self.publish(), answer)
        self.assertTrue(response.data['is_completed'])
        self.assertAlmostEqual(response.data['result']['psi_value'], 2 / 3, places=4)

    def test_required_check_failure_or_error_does_not_complete(self):
        for result in ({'value': 1274}, {'error': 'NameError: df'}, {'value': '1275'}, {'value': True}):
            with self.subTest(result=result):
                self.client.force_login(self.admin)
                LearningItem.objects.filter(pk=self.item.pk).update(practice=None, practice_version=None)
                answer = deepcopy(self.answer)
                answer['results'][0] = result
                response = self.send(self.publish(), answer)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertFalse(response.data['is_completed'])
                self.assertFalse(response.data['submission']['criteria_snapshot'][0]['checked'])

    def test_malformed_submissions_are_rejected(self):
        submission = self.publish()
        for answer in ({'code': ['x'], 'results': self.answer['results']},
                       {**self.answer, 'results': self.answer['results'][:2]},
                       {**self.answer, 'results': [{'value': {'a': 1}}] * 3},
                       {**self.answer, 'results': [{'value': 1, 'error': 'x'}] * 3},
                       {**self.answer, 'extra': True}):
            with self.subTest(answer=answer):
                self.assertEqual(self.send(submission, answer).status_code, 400)

    def test_invalid_configs_are_rejected(self):
        plugin = registry.get('python_lab')
        bad = []
        for change in ({'packages': ['os']}, {'packages': ['pandas', 'pandas']}, {'steps': []},
                       {'datasets': [{'media': 999, 'name': 'x.csv', 'size': 1, 'url': '/x'}]}):
            bad.append({**self.config, **change})
        no_checks = deepcopy(self.config)
        for step in no_checks['steps']:
            step['checks'] = []
        bad.append(no_checks)
        multiline = deepcopy(self.config)
        multiline['steps'][0]['checks'][0]['expression'] = 'import os\nos.getcwd()'
        bad.append(multiline)
        negative = deepcopy(self.config)
        negative['steps'][0]['checks'][0]['tolerance'] = -1
        bad.append(negative)
        nested = deepcopy(self.config)
        nested['steps'][0]['checks'][0]['expected'] = [[1]]
        bad.append(nested)
        for config in bad:
            with self.subTest(config=config):
                with self.assertRaises(Exception):
                    plugin.validate_config(config)

    def test_uploaded_csv_dataset_is_accepted(self):
        media = MediaFile.objects.create(file='lab/laptops.csv', original_name='laptops.csv',
                                         content_type='text/csv', size=2048, uploaded_by=self.admin)
        # Same shape as the upload response of /api/v1/media/files/, including its `id`.
        config = {**self.config, 'datasets': [{'id': media.pk, 'media': media.pk, 'name': 'laptops.csv', 'size': 2048,
                                               'url': f'/api/v1/media/{media.pk}/download/'}]}
        registry.get('python_lab').validate_config(config)

    def test_learner_can_download_dataset_of_published_practice(self):
        from tempfile import TemporaryDirectory
        from django.core.files.base import ContentFile
        # On Windows a file that is still open cannot be deleted, so cleanup must not fail the test.
        folder = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        override = override_settings(MEDIA_ROOT=folder.name)
        override.enable()
        self.addCleanup(override.disable)
        media = MediaFile(original_name='cars.csv', content_type='text/csv', size=12, uploaded_by=self.admin)
        media.file.save('cars.csv', ContentFile(b'Year,Price\n1,2'), save=True)
        url = f'/api/v1/media/{media.pk}/download/'
        self.publish({**self.config, 'datasets': [{'media': media.pk, 'name': 'cars.csv', 'size': media.size, 'url': url}]})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)
        response.close()
        media.file.close()
