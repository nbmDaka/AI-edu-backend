from copy import copy
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.education.models import Course, LearningItem, LearningTrack, Module, PracticeSubmission
from .models import PracticeDefinition, PracticeVersion
from .registry import registry


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PracticeSelectionTests(TestCase):
    def setUp(self):
        # Extra registered plugins exercise the same catalog contract as future practices.
        variants = {}
        for name in ('second_workshop', 'third_workshop'):
            plugin = copy(registry.get('lecture_workshop'))
            plugin.manifest = {**plugin.manifest, 'type': name, 'name': name}
            variants[(name, '1.0.0')] = plugin
        registry_patch = patch.dict(registry._plugins, variants)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        track = LearningTrack.objects.create(title='Track', is_published=True)
        course = Course.objects.create(title='Course', learning_track=track, is_published=True)
        module = Module.objects.create(title='Module', course=course, is_published=True)
        self.item = LearningItem.objects.create(title='Assignment', module=module, type='PRACTICE')
        admin = User.objects.create_user('admin@selection.test', 'Password123!', role='ADMIN')
        student = User.objects.create_user('student@selection.test', 'Password123!', learning_track=track)
        other = User.objects.create_user('other@selection.test', 'Password123!', learning_track=track)
        self.admin, self.student, self.other = APIClient(), APIClient(), APIClient()
        self.admin.force_login(admin)
        self.student.force_login(student)
        self.other.force_login(other)
        self.path = f'/api/v1/items/{self.item.short_id}/practice/'
        self.payload = {'practice_type': 'lecture_workshop', 'plugin_version': '1.0.0',
                        'title': 'Original title', 'description': 'Original instructions', 'config': {}}

    def save(self, kind):
        response = self.admin.put(self.path + 'draft/', {**self.payload, 'practice_type': kind}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def publish(self):
        response = self.admin.post(self.path + 'draft/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_switching_between_three_available_practices_keeps_the_same_learning_item(self):
        self.assertEqual(len(self.admin.get('/api/v1/practice-types/').data), 7)
        versions = []
        for kind in ('lecture_workshop', 'second_workshop', 'third_workshop', 'lecture_workshop'):
            draft = self.save(kind)
            reopened = self.admin.get(self.path + 'draft/').data
            self.assertEqual(reopened['draft']['practice_type'], kind)
            self.assertEqual(reopened['draft']['id'], draft['id'])
            published = self.publish()
            versions.append(published)
            self.assertEqual(self.student.get(self.path).data['version']['practice_type'], kind)
            self.item.refresh_from_db()
            self.assertEqual(self.item.title, self.payload['title'])
            self.assertEqual(self.item.description, self.payload['description'])
        self.assertEqual(LearningItem.objects.count(), 1)
        self.assertEqual(len({version['practice_definition'] for version in versions}), 4)
        for version in versions:
            record = PracticeVersion.objects.get(pk=version['id'])
            self.assertEqual(record.practice_definition.practice_type, version['practice_type'])

    def test_draft_replacement_does_not_change_publication_or_started_attempts(self):
        self.save('lecture_workshop')
        original = self.publish()
        started = self.student.post(self.path + 'start/').data['submission']['id']
        replacement = self.save('second_workshop')
        self.assertNotEqual(replacement['practice_definition'], original['practice_definition'])
        self.assertEqual(self.other.get(self.path).data['version']['id'], original['id'])
        reopened = self.admin.get(self.path + 'draft/').data
        self.assertEqual(reopened['published']['practice_type'], 'lecture_workshop')
        self.assertEqual(reopened['draft']['practice_type'], 'second_workshop')
        self.publish()
        self.assertEqual(self.student.get(self.path).data['version']['id'], original['id'])
        response = self.student.post(self.path, {'submission_id': started, 'submission_data': {'completed': True}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        historic = PracticeSubmission.objects.get(pk=started)
        self.assertEqual(historic.practice_version_id, original['id'])
        self.assertEqual(historic.practice_version.practice_definition.practice_type, 'lecture_workshop')
        self.assertEqual(historic.result.effective_score, 1)
        response = self.student.post(self.path + 'start/')
        self.assertEqual(response.data['version']['practice_type'], 'second_workshop')
        self.assertEqual(self.other.post(self.path + 'start/').data['version']['practice_type'], 'second_workshop')

    def test_unknown_or_invalid_replacement_does_not_change_current_assignment(self):
        self.save('lecture_workshop')
        original = self.publish()
        for changes in ({'practice_type': 'missing_workshop'}, {'practice_type': 'second_workshop', 'config': {'invalid': True}}):
            response = self.admin.put(self.path + 'draft/', {**self.payload, **changes}, format='json')
            self.assertEqual(response.status_code, 400)
            self.item.refresh_from_db()
            self.assertEqual(self.item.practice_version_id, original['id'])
            self.assertEqual(self.item.practice_id, original['practice_definition'])
        self.assertEqual(PracticeDefinition.objects.count(), 1)
        self.assertEqual(PracticeVersion.objects.count(), 1)
