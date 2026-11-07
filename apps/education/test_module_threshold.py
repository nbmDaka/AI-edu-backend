from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.education.models import Course, LearningTrack, Module


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ModuleThresholdTests(TestCase):
    def setUp(self):
        track = LearningTrack.objects.create(title='Track', is_published=True)
        course = Course.objects.create(title='Course', learning_track=track, is_published=True, adaptive_learning_enabled=True)
        self.module = Module.objects.create(course=course, title='Module', is_published=True)
        self.admin = User.objects.create_user('admin@role.test', 'Password123!', role='ADMIN')
        self.student = User.objects.create_user('student@role.test', 'Password123!', learning_track=track)
        self.url = f'/api/v1/modules/{self.module.short_id}/threshold/'

    def client_for(self, user):
        client = APIClient()
        client.force_login(user)
        return client

    def test_admin_changes_the_threshold_within_limits(self):
        admin = self.client_for(self.admin)
        response = admin.patch(self.url, {'adaptive_threshold': 70}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.module.refresh_from_db()
        self.assertEqual(self.module.adaptive_threshold, 70)
        for value in (39, 81, None):
            self.assertEqual(admin.patch(self.url, {'adaptive_threshold': value}, format='json').status_code, 400)
        self.assertEqual(admin.patch(self.url, {}, format='json').status_code, 400)

    def test_students_cannot_change_the_threshold_or_read_analytics(self):
        student = self.client_for(self.student)
        self.assertEqual(student.patch(self.url, {'adaptive_threshold': 50}, format='json').status_code, 403)
        self.assertEqual(student.get('/api/v1/admin/analytics/').status_code, 403)

    def test_teacher_role_no_longer_exists(self):
        payload = {'email': 'teacher@role.test', 'first_name': 'A', 'last_name': 'T', 'password': 'Password123!',
                   'role': 'TEACHER', 'is_active': True}
        self.assertEqual(self.client_for(self.admin).post('/api/v1/admin/users/', payload, format='json').status_code, 400)
