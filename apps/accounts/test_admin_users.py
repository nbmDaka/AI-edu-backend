from types import SimpleNamespace

from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.assessments.models import Test, TestAttempt, Question, Option
from apps.education.models import (LearningTrack, Course, Module, LearningItem, Lesson,
    UserLearningItemProgress, PracticeSubmission, UserModuleProgress, UserCompetencyProgress)
from apps.lessons.models import LessonBlock
from apps.mediafiles.models import MediaFile
from .models import User, UserCourseAccess
from .admin_views import admin_user_queryset
from .admin_serializers import AdminUserSerializer


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AdminUsersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.track = LearningTrack.objects.create(title='IT', is_published=True)
        cls.other_track = LearningTrack.objects.create(title='Business', is_published=True)
        cls.course = Course.objects.create(title='Python', learning_track=cls.track, is_published=True)
        cls.second_course = Course.objects.create(title='AI', learning_track=cls.track, is_published=True)
        cls.draft = Course.objects.create(title='Draft', learning_track=cls.track)
        cls.foreign_course = Course.objects.create(title='Finance', learning_track=cls.other_track, is_published=True)
        cls.module = Module.objects.create(course=cls.second_course, title='Module', is_published=True)
        cls.lesson = Lesson.objects.create(module=cls.module, title='Lecture', status='PUBLISHED')
        cls.lecture = LearningItem.objects.create(module=cls.module, type='LECTURE', title='Lecture',
            position=0, status='PUBLISHED', lesson=cls.lesson)
        cls.practice = LearningItem.objects.create(module=cls.module, type='PRACTICE', title='Practice',
            position=1, status='PUBLISHED', practice_criteria=['Done'])
        cls.test = Test.objects.create(title='Test', is_published=True)
        cls.test_item = LearningItem.objects.create(module=cls.module, type='TEST', title='Test',
            position=2, status='PUBLISHED', test=cls.test)
        cls.question = Question.objects.create(test=cls.test, text='Question', position=0)
        cls.correct = Option.objects.create(question=cls.question, text='Yes', is_correct=True, position=0)
        cls.admin = User.objects.create_user('admin@example.test', 'AdminPassword123!', role='ADMIN')
        cls.student = User.objects.create_user('student@example.test', 'StudentPassword123!',
            first_name='Ada', last_name='Lovelace', learning_track=cls.track)
        cls.media = MediaFile.objects.create(original_name='private.png', content_type='image/png',
            size=1, file='private.png', uploaded_by=cls.admin)
        LessonBlock.objects.create(lesson=cls.lesson, type='IMAGE', position=0, media=cls.media)

    def setUp(self):
        self.admin_client, self.student_client = APIClient(), APIClient()
        self.admin_client.force_login(self.admin)
        self.student_client.force_login(self.student)
        self.url = f'/api/v1/admin/users/{self.student.pk}/'

    def patch(self, data):
        return self.admin_client.patch(self.url, data, format='json')

    def custom(self, ids):
        response = self.patch({'course_access_mode': 'CUSTOM', 'course_ids': ids})
        self.assertEqual(response.status_code, 200, response.data)

    def create_payload(self, **overrides):
        return {'email': 'new@example.test', 'password': 'SafeTemporaryWord842!', 'first_name': 'New',
            'role': 'STUDENT', 'learning_track': self.track.pk, **overrides}

    def test_student_and_anonymous_cannot_access_any_admin_user_endpoint(self):
        for client in [self.student_client, APIClient()]:
            for url in ['/api/v1/admin/users/', self.url, self.url + 'learning-progress/']:
                self.assertEqual(client.get(url).status_code, 403)
            self.assertEqual(client.post('/api/v1/admin/users/', self.create_payload(), format='json').status_code, 403)
            self.assertEqual(client.patch(self.url, {'role': 'ADMIN'}, format='json').status_code, 403)

    def test_admin_list_is_paginated_filtered_and_has_no_passwords(self):
        response = self.admin_client.get('/api/v1/admin/users/?page_size=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertIsNotNone(response.data['next'])
        response = self.admin_client.get(f'/api/v1/admin/users/?search=Ada+Lovelace&role=STUDENT&learning_track={self.track.pk}&status=active')
        self.assertEqual([row['id'] for row in response.data['results']], [self.student.pk])
        self.assertNotIn('password', response.data['results'][0])
        self.assertNotIn('is_superuser', response.data['results'][0])
        self.student.refresh_from_db()
        self.assertEqual(response.data['results'][0]['last_login'], self.student.last_login.isoformat().replace('+00:00', 'Z'))

    def test_list_serialization_avoids_n_plus_one_queries(self):
        for index in range(8):
            user = User.objects.create_user(f'list-{index}@example.test', role='STUDENT', learning_track=self.track,
                course_access_mode='CUSTOM')
            UserCourseAccess.objects.create(user=user, course=self.course, granted_by=self.admin)
        with self.assertNumQueries(2):
            rows = AdminUserSerializer(list(admin_user_queryset()), many=True,
                context={'request': SimpleNamespace(user=self.admin)}).data
        self.assertTrue(any(row['accessible_course_count'] == 1 for row in rows))

    def test_create_student_and_platform_admin_without_django_privileges(self):
        response = self.admin_client.post('/api/v1/admin/users/', self.create_payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = User.objects.get(pk=response.data['id'])
        self.assertEqual(created.learning_track_id, self.track.pk)
        self.assertEqual(created.course_access_mode, 'TRACK_DEFAULT')
        self.assertTrue(created.check_password('SafeTemporaryWord842!'))
        response = self.admin_client.post('/api/v1/admin/users/', self.create_payload(
            email='newadmin@example.test', role='ADMIN', learning_track=None), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = User.objects.get(pk=response.data['id'])
        self.assertEqual(created.role, 'ADMIN')
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertNotIn('password', response.data)

    def test_student_requires_available_track_and_safe_password(self):
        disabled = LearningTrack.objects.create(title='Disabled', is_published=True, is_active=False)
        for changes in [{'learning_track': None}, {'learning_track': 999999}, {'learning_track': disabled.pk},
                        {'password': '12345678'}, {'password': 'short'}]:
            response = self.admin_client.post('/api/v1/admin/users/', self.create_payload(**changes), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.patch({'learning_track': disabled.pk}).status_code, 400)

    def test_django_privilege_fields_and_unknown_roles_are_rejected(self):
        for key, value in {'is_superuser': True, 'is_staff': True, 'groups': [1], 'user_permissions': [1],
                           'password_hash': 'hash', 'is_protected': False, 'date_joined': '2026-01-01'}.items():
            self.assertEqual(self.patch({key: value}).status_code, 400)
            payload = self.create_payload(**{key: value})
            self.assertEqual(self.admin_client.post('/api/v1/admin/users/', payload, format='json').status_code, 400)
        self.assertEqual(self.patch({'role': 'SUPERUSER'}).status_code, 400)
        public = APIClient()
        response = public.post('/api/v1/auth/register/', self.create_payload(is_superuser=True), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.student_client.patch('/api/v1/auth/me/', {'role': 'ADMIN'}, format='json').status_code, 400)
        self.assertEqual(self.student_client.patch('/api/v1/auth/me/', {'course_access_mode': 'TRACK_DEFAULT'}, format='json').status_code, 400)

    def test_default_access_preserves_published_track_courses(self):
        response = self.student_client.get('/api/v1/courses/')
        self.assertEqual({row['id'] for row in response.data['results']}, {self.course.pk, self.second_course.pk})
        self.assertEqual(self.student_client.get(f'/api/v1/courses/{self.second_course.slug}/').status_code, 200)
        self.assertEqual(self.student_client.get(f'/api/v1/courses/{self.draft.slug}/').status_code, 404)
        self.assertEqual(self.student_client.get(f'/api/v1/courses/{self.foreign_course.slug}/').status_code, 404)

    def test_custom_access_limits_catalog_and_course_filter(self):
        self.custom([self.course.pk])
        response = self.student_client.get('/api/v1/courses/')
        self.assertEqual([row['id'] for row in response.data['results']], [self.course.pk])
        allowed = self.admin_client.get(f'/api/v1/admin/users/?course={self.course.pk}')
        denied = self.admin_client.get(f'/api/v1/admin/users/?course={self.second_course.pk}')
        self.assertEqual([row['id'] for row in allowed.data['results']], [self.student.pk])
        self.assertEqual(denied.data['count'], 0)
        self.custom([])
        self.assertEqual(self.student_client.get('/api/v1/courses/').data['count'], 0)

    def test_denied_course_cannot_be_opened_through_any_content_path(self):
        self.custom([self.course.pk])
        urls = [f'courses/{self.second_course.slug}/', f'courses/{self.second_course.short_id}/',
            f'courses/{self.second_course.slug}/progress/', f'modules/{self.module.short_id}/',
            f'items/{self.lecture.short_id}/', f'lessons/{self.lesson.short_id}/',
            f'lessons/{self.lesson.short_id}/blocks/', f'items/{self.test_item.short_id}/test/',
            f'items/{self.test_item.short_id}/attempts/', f'items/{self.practice.short_id}/practice/',
            f'tests/{self.test.pk}/questions/', f'media/{self.media.pk}/']
        for path in urls:
            with self.subTest(path=path):
                self.assertIn(self.student_client.get('/api/v1/' + path).status_code, [403, 404])
        for resource in ['modules', 'items', 'lessons']:
            self.assertEqual(self.student_client.get(f'/api/v1/{resource}/').data['count'], 0)
        self.assertEqual(self.student_client.patch(f'/api/v1/items/{self.lecture.short_id}/progress/',
            {'progress_percent': 100}, format='json').status_code, 404)
        self.assertEqual(self.student_client.post(f'/api/v1/items/{self.practice.short_id}/practice/',
            {'checks': [True]}, format='json').status_code, 404)
        self.assertEqual(self.student_client.post(f'/api/v1/items/{self.test_item.short_id}/attempts/',
            {'answers': []}, format='json').status_code, 404)
        self.assertFalse(TestAttempt.objects.exists())
        self.assertFalse(PracticeSubmission.objects.exists())

    def test_admin_keeps_content_access_independent_of_grants(self):
        self.custom([])
        self.assertEqual(self.admin_client.get(f'/api/v1/courses/{self.draft.slug}/').status_code, 200)
        self.assertEqual(self.admin_client.get(f'/api/v1/items/{self.lecture.short_id}/').status_code, 200)

    def test_course_assignments_reject_foreign_draft_missing_and_duplicate_ids(self):
        for ids in [[self.foreign_course.pk], [self.draft.pk], [999999], [self.course.pk, self.course.pk]]:
            self.assertEqual(self.patch({'course_access_mode': 'CUSTOM', 'course_ids': ids}).status_code, 400)
        self.assertEqual(self.patch({'course_ids': [self.course.pk]}).status_code, 400)
        self.assertEqual(self.patch({'course_access_mode': 'INVALID'}).status_code, 400)
        self.assertFalse(UserCourseAccess.objects.exists())

    def test_trajectory_change_revokes_old_grants_and_preserves_rows(self):
        self.custom([self.course.pk])
        response = self.patch({'learning_track': self.other_track.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(UserCourseAccess.objects.get(user=self.student, course=self.course).is_active)
        self.assertEqual(self.student_client.get('/api/v1/courses/').data['count'], 0)
        response = self.patch({'course_ids': [self.foreign_course.pk]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['id'] for row in self.student_client.get('/api/v1/courses/').data['results']], [self.foreign_course.pk])
        self.assertEqual(self.patch({'learning_track': self.track.pk}).status_code, 200)
        self.assertEqual(self.student_client.get('/api/v1/courses/').data['count'], 0)
        self.custom([self.course.pk])
        self.assertEqual(UserCourseAccess.objects.filter(user=self.student, course=self.course).count(), 1)

    def test_profile_track_change_cannot_bypass_custom_access(self):
        self.custom([self.course.pk])
        response = self.student_client.patch('/api/v1/auth/me/', {'learning_track': self.other_track.pk}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserCourseAccess.objects.get(user=self.student, course=self.course).is_active)
        self.assertEqual(self.student_client.get('/api/v1/courses/').data['count'], 0)

    def test_revoke_regrant_keeps_unique_grant_with_actor_and_timestamp(self):
        self.custom([self.course.pk])
        original = UserCourseAccess.objects.get(user=self.student, course=self.course)
        self.assertEqual(original.granted_by_id, self.admin.pk)
        self.custom([])
        self.custom([self.course.pk])
        renewed = UserCourseAccess.objects.get(pk=original.pk)
        self.assertTrue(renewed.is_active)
        self.assertGreaterEqual(renewed.granted_at, original.granted_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserCourseAccess.objects.create(user=self.student, course=self.course)

    def test_course_moved_or_unpublished_cannot_use_stale_grant(self):
        self.custom([self.course.pk])
        self.course.learning_track = self.other_track
        self.course.save()
        self.assertEqual(self.student_client.get('/api/v1/courses/').data['count'], 0)
        self.assertEqual(self.student_client.get(f'/api/v1/courses/{self.course.slug}/').status_code, 404)

    def test_deactivation_blocks_login_existing_sessions_and_preserves_all_history(self):
        attempt = TestAttempt.objects.create(user=self.student, test=self.test, test_version=1,
            answers=[], snapshot=[], earned_points=1, total_points=1, percent=100, passed=True)
        progress = UserLearningItemProgress.objects.create(user=self.student, learning_item=self.lecture,
            progress_percent=96, is_completed=True)
        submission = PracticeSubmission.objects.create(user=self.student, item=self.practice, criteria=['Done'], checks=[True])
        module_progress = UserModuleProgress.objects.create(user=self.student, module=self.module, is_completed=True)
        competency = UserCompetencyProgress.objects.create(user=self.student, course=self.second_course, competency='Python', score=.8)
        self.assertEqual(self.patch({'is_active': False}).status_code, 200)
        self.assertIsNone(authenticate(email=self.student.email, password='StudentPassword123!'))
        self.assertEqual(self.student_client.get('/api/v1/auth/me/').status_code, 403)
        response = APIClient().post('/api/v1/auth/login/', {'email': self.student.email, 'password': 'StudentPassword123!'}, format='json')
        self.assertEqual(response.status_code, 400)
        for record in [attempt, progress, submission, module_progress, competency]:
            record.refresh_from_db()
        self.assertEqual(progress.progress_percent, 96)
        self.assertEqual(self.admin_client.delete(self.url).status_code, 405)
        self.assertEqual(self.patch({'is_active': True}).status_code, 200)
        self.assertIsNotNone(authenticate(email=self.student.email, password='StudentPassword123!'))

    def test_admin_cannot_change_own_role_status_or_system_superuser(self):
        own = f'/api/v1/admin/users/{self.admin.pk}/'
        for data in [{'is_active': False}, {'role': 'STUDENT', 'learning_track': self.track.pk}]:
            self.assertEqual(self.admin_client.patch(own, data, format='json').status_code, 400)
        system = User.objects.create_superuser('system@example.test', 'SystemPassword123!')
        url = f'/api/v1/admin/users/{system.pk}/'
        self.assertTrue(self.admin_client.get(url).data['is_protected'])
        self.assertEqual(self.admin_client.patch(url, {'is_active': False}, format='json').status_code, 400)
        self.assertEqual(self.admin_client.delete(url).status_code, 405)

    def test_changing_student_to_admin_revokes_grants_without_django_privileges(self):
        self.custom([self.course.pk])
        self.assertEqual(self.patch({'role': 'ADMIN'}).status_code, 200)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.learning_track_id)
        self.assertFalse(self.student.is_staff)
        self.assertFalse(self.student.is_superuser)
        self.assertFalse(UserCourseAccess.objects.get(user=self.student, course=self.course).is_active)
        self.assertEqual(self.patch({'role': 'STUDENT'}).status_code, 400)
        self.assertEqual(self.patch({'role': 'STUDENT', 'learning_track': self.track.pk}).status_code, 200)

    def test_real_learning_progress_is_paginated_and_kept_after_access_revocation(self):
        progress = UserLearningItemProgress.objects.create(user=self.student, learning_item=self.lecture, progress_percent=54)
        self.custom([])
        response = self.admin_client.get(self.url + 'learning-progress/?page_size=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['id'], progress.pk)
        self.assertEqual(row['progress_percent'], 54)
        self.assertEqual(row['course_title'], self.second_course.title)
        self.assertFalse(row['is_completed'])

    def test_case_insensitive_email_duplicates_and_invalid_filters_are_rejected(self):
        self.assertEqual(self.patch({'email': self.admin.email.upper()}).status_code, 400)
        self.assertEqual(self.patch({'email': 'CHANGED@example.test'}).status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.email, 'changed@example.test')
        for query in ['role=SUPERUSER', 'status=broken', 'learning_track=abc', 'course=-1']:
            self.assertEqual(self.admin_client.get('/api/v1/admin/users/?' + query).status_code, 400)
