from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activities.testing import configure_checklist
from apps.assessments.models import Test, TestAttempt
from .analytics import learning_analytics
from .models import Course, LearningItem, LearningTrack, Module, PracticeSubmission, UserLearningItemProgress


@override_settings(SECURE_SSL_REDIRECT=False)
class LearningAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.track = LearningTrack.objects.create(title='Analytics IT', is_published=True)
        cls.other_track = LearningTrack.objects.create(title='Analytics Business', is_published=True)
        cls.course = Course.objects.create(title='Analytics Python', learning_track=cls.track, is_published=True)
        cls.other_course = Course.objects.create(title='Analytics Finance', learning_track=cls.other_track, is_published=True)
        cls.empty_course = Course.objects.create(title='Empty', learning_track=cls.track, is_published=True)
        cls.module = Module.objects.create(course=cls.course, title='Basics', is_published=True)
        cls.empty_module = Module.objects.create(course=cls.course, title='Empty', is_published=True, position=1)
        cls.lecture = LearningItem.objects.create(module=cls.module, title='Lecture', type='LECTURE', status='PUBLISHED')
        cls.test = Test.objects.create(title='Test', is_published=True)
        cls.test_item = LearningItem.objects.create(module=cls.module, title='Test', type='TEST', status='PUBLISHED', position=1, test=cls.test)
        cls.practice = LearningItem.objects.create(module=cls.module, title='Practice', type='PRACTICE', status='PUBLISHED', position=2)
        cls.admin = User.objects.create_user('analytics-admin@example.test', role='ADMIN')
        cls.version = configure_checklist(cls.practice, ['Done'], cls.admin)
        cls.finished = User.objects.create_user('analytics-finished@example.test', learning_track=cls.track, is_active=False)
        cls.failed = User.objects.create_user('analytics-failed@example.test', learning_track=cls.track)
        cls.practising = User.objects.create_user('analytics-practice@example.test', learning_track=cls.track)
        cls.other = User.objects.create_user('analytics-other@example.test', learning_track=cls.other_track)
        cls.now = timezone.now()
        cls.old = cls.now - timedelta(days=45)
        for item in [cls.lecture, cls.test_item, cls.practice]:
            row = UserLearningItemProgress.objects.create(user=cls.finished, learning_item=item,
                is_completed=True, progress_percent=100, completed_at=cls.old)
            UserLearningItemProgress.objects.filter(pk=row.pk).update(updated_at=cls.old)
        for _ in range(2):
            TestAttempt.objects.create(user=cls.failed, test=cls.test, test_version=1, answers=[], snapshot=[],
                earned_points=0, total_points=1, percent=0, passed=False)
        PracticeSubmission.objects.create(user=cls.practising, item=cls.practice, practice_version=cls.version)
        cls.now = timezone.now()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = '/api/v1/admin/analytics/'

    def report(self, **filters):
        response = self.client.get(self.url, filters)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_admin_only_and_read_only(self):
        for user in [None, self.failed]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
        self.assertEqual(self.client.get(self.url)['Cache-Control'], 'private, no-store')

    def test_unique_students_includes_failed_attempt_and_unfinished_practice(self):
        data = self.report(track=self.track.pk)
        self.assertEqual(data['summary'], {'total_students': 4, 'started_students': 3, 'active_students': 2,
            'completed_students': 1, 'course_count': 2, 'module_count': 2, 'item_count': 3})
        row = next(row for row in data['courses'] if row['id'] == self.course.pk)
        self.assertEqual(row['started_students'], 3)
        self.assertEqual(row['active_students'], 2)
        self.assertEqual(row['completed_students'], 1)
        self.assertEqual(row['completion_rate'], 33.3)
        self.assertEqual(data['tracks'][0]['assigned_students'], 3)
        self.assertNotIn('email', str(data))
        self.assertNotIn('user_id', str(data))

    def test_completion_is_cumulative_and_empty_course_never_completed(self):
        data = self.report(period='7d', course=self.course.pk)
        self.assertEqual(data['summary']['completed_students'], 1)
        empty = self.report(course=self.empty_course.pk)
        self.assertEqual(empty['courses'][0]['completed_students'], 0)
        self.assertEqual(empty['courses'][0]['completion_rate'], 0)
        self.assertEqual(empty['modules'], [])
        data = self.report(course=self.course.pk)
        self.assertEqual(data['modules'][0]['completed_students'], 1)
        self.assertEqual(data['modules'][1]['completed_students'], 0)

    def test_period_only_changes_activity(self):
        recent = self.report(period='30d', track=self.track.pk)
        for period in ['90d', 'all']:
            data = self.report(period=period, track=self.track.pk)
            self.assertEqual(data['summary']['active_students'], 3)
            self.assertEqual(data['summary']['started_students'], recent['summary']['started_students'])
            self.assertEqual(data['summary']['completed_students'], recent['summary']['completed_students'])

    def test_inclusive_period_boundary_and_future_activity(self):
        row = UserLearningItemProgress.objects.create(user=self.other, learning_item=self.lecture)
        boundary = self.now - timedelta(days=7)
        UserLearningItemProgress.objects.filter(pk=row.pk).update(updated_at=boundary)
        with patch('apps.education.analytics.timezone.now', return_value=self.now):
            self.assertEqual(learning_analytics(course=self.course.pk, period='7d')['summary']['active_students'], 3)
        UserLearningItemProgress.objects.filter(pk=row.pk).update(updated_at=boundary - timedelta(seconds=1))
        with patch('apps.education.analytics.timezone.now', return_value=self.now):
            self.assertEqual(learning_analytics(course=self.course.pk, period='7d')['summary']['active_students'], 2)
        UserLearningItemProgress.objects.filter(pk=row.pk).update(updated_at=self.now + timedelta(days=1))
        with patch('apps.education.analytics.timezone.now', return_value=self.now):
            self.assertEqual(learning_analytics(course=self.course.pk, period='all')['summary']['started_students'], 3)

    def test_students_deduplicated_across_courses_and_track_assignment_can_change(self):
        module = Module.objects.create(course=self.other_course, title='Finance', is_published=True)
        item = LearningItem.objects.create(module=module, title='Finance lecture', type='LECTURE', status='PUBLISHED')
        UserLearningItemProgress.objects.create(user=self.failed, learning_item=item)
        self.failed.learning_track = self.other_track
        self.failed.save()
        data = self.report()
        self.assertEqual(data['summary']['started_students'], 3)
        self.assertEqual(sum(row['started_students'] for row in data['courses']), 4)
        self.assertEqual(self.report(course=self.other_course.pk)['summary']['started_students'], 1)

    def test_invalid_filters_return_400(self):
        for filters in [{'period': '1d'}, {'track': '-1'}, {'course': 'bad'}, {'track': '999999'},
                        {'course': '999999'}, {'track': self.other_track.pk, 'course': self.course.pk}]:
            self.assertEqual(self.client.get(self.url, filters).status_code, 400, filters)

    def test_deleted_users_and_admin_activity_are_excluded(self):
        UserLearningItemProgress.objects.create(user=self.admin, learning_item=self.lecture)
        self.failed.delete()
        data = self.report(track=self.track.pk)
        self.assertEqual(data['summary']['total_students'], 3)
        self.assertEqual(data['summary']['started_students'], 2)
        self.assertEqual(data['summary']['active_students'], 1)

    def test_drafts_deleted_content_and_inactive_tracks_are_excluded(self):
        draft = LearningItem.objects.create(module=self.module, title='Draft', type='LECTURE', position=3)
        UserLearningItemProgress.objects.create(user=self.other, learning_item=draft)
        draft_module = Module.objects.create(course=self.course, title='Draft')
        draft_item = LearningItem.objects.create(module=draft_module, title='Hidden', type='LECTURE', status='PUBLISHED')
        UserLearningItemProgress.objects.create(user=self.other, learning_item=draft_item)
        deleted = LearningItem.objects.create(module=self.module, title='Deleted', type='LECTURE', status='PUBLISHED', position=4)
        UserLearningItemProgress.objects.create(user=self.other, learning_item=deleted)
        deleted.delete()
        self.assertEqual(self.report(course=self.course.pk)['summary']['started_students'], 3)
        self.assertEqual(self.report(course=self.course.pk)['summary']['item_count'], 3)
        self.track.is_active = False
        self.track.save()
        self.assertEqual(self.report()['summary']['started_students'], 0)
        self.assertEqual(self.client.get(self.url, {'course': self.course.pk}).status_code, 400)

    def test_query_count_does_not_grow_per_course_or_student(self):
        with CaptureQueriesContext(connection) as first:
            learning_analytics()
        for index in range(6):
            course = Course.objects.create(title=f'Extra {index}', learning_track=self.track, is_published=True)
            module = Module.objects.create(course=course, title='Module', is_published=True)
            item = LearningItem.objects.create(module=module, title='Lecture', type='LECTURE', status='PUBLISHED')
            UserLearningItemProgress.objects.create(user=self.failed, learning_item=item)
        with CaptureQueriesContext(connection) as second:
            learning_analytics()
        self.assertEqual(len(first), len(second))
        self.assertLessEqual(len(second), 10)
