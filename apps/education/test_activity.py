from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ActivityEvent
from . import test_adaptive


class ActivityLogTests(TestCase):
    make_lecture = test_adaptive.AdaptiveLearningTests.make_lecture
    prepare = test_adaptive.AdaptiveLearningTests.prepare
    submit = test_adaptive.AdaptiveLearningTests.submit

    def setUp(self):
        test_adaptive.AdaptiveLearningTests.setUp(self)
        ActivityEvent.objects.all().delete()  # force_login in setUp counts as a login

    def types(self, user):
        return list(ActivityEvent.objects.filter(user=user).order_by('created_at', 'id').values_list('type', flat=True))

    def test_learning_flow_is_logged_once_per_action(self):
        url = f'/api/v1/items/{self.lecture.short_id}/progress/'
        for percent in (10, 50):
            self.assertEqual(self.client.patch(url, {'progress_percent': percent}, format='json').status_code, 200)
        self.assertEqual(self.types(self.student), ['LECTURE_STARTED'])
        self.prepare()
        self.prepare()  # repeated progress does not add events
        self.submit(2)
        self.assertEqual(self.types(self.student),
                         ['LECTURE_STARTED', 'LECTURE_COMPLETED', 'PRACTICE_SUBMITTED', 'PRACTICE_SUBMITTED', 'TEST_SUBMITTED'])
        test_event = ActivityEvent.objects.get(user=self.student, type='TEST_SUBMITTED')
        self.assertEqual((test_event.course_id, test_event.module_id, test_event.item_id),
                         (self.course.pk, self.first.pk, self.test_item.pk))
        self.assertEqual((test_event.data['readiness'], test_event.data['module_passed'], test_event.data['passed']),
                         (43.5, False, False))
        practice = ActivityEvent.objects.filter(type='PRACTICE_SUBMITTED').first()
        self.assertEqual((practice.data['score'], practice.data['completed']), (1, True))

    def test_login_is_logged(self):
        client = APIClient()
        client.get('/api/v1/csrf/')
        response = client.post('/api/v1/auth/login/', {'email': 'adaptive@example.test', 'password': 'Password1234!'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.types(self.student), ['LOGIN'])
        self.admin_client.force_login(self.admin)
        self.assertFalse(ActivityEvent.objects.filter(user=self.admin).exists())

    def test_admin_list_filters_and_summary(self):
        self.prepare()
        self.submit(2)
        self.client.force_login(self.other)
        self.prepare()
        old = ActivityEvent.objects.create(user=self.other, type='LOGIN')
        ActivityEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
        rows = self.admin_client.get('/api/v1/admin/activity/').data['results']
        self.assertEqual(len(rows), 8)  # includes the force_login of the second student
        self.assertEqual(rows[0]['user_email'], 'adaptive-other@example.test')
        self.assertEqual(len(self.admin_client.get('/api/v1/admin/activity/?period=all').data['results']), 9)
        tests = self.admin_client.get('/api/v1/admin/activity/?type=TEST_SUBMITTED').data['results']
        self.assertEqual([(row['item_title'], row['module_title'], row['data']['readiness']) for row in tests],
                         [('Test', 'First', 43.5)])
        mine = self.admin_client.get(f'/api/v1/admin/activity/?user={self.student.pk}').data['results']
        self.assertEqual({row['user_id'] for row in mine}, {self.student.pk})
        self.assertEqual(len(self.admin_client.get('/api/v1/admin/activity/?search=other').data['results']), 4)
        self.assertEqual(self.admin_client.get('/api/v1/admin/activity/?period=1y').status_code, 400)
        self.assertEqual(self.admin_client.get('/api/v1/admin/activity/?type=NOPE').status_code, 400)
        summary = self.admin_client.get('/api/v1/admin/activity/summary/').data
        self.assertEqual((summary['totals']['PRACTICE_SUBMITTED'], summary['totals']['LOGIN'], summary['active_users']), (2, 1, 2))
        self.assertEqual(len(summary['days']), 14)
        self.assertEqual(summary['days'][-1]['total'], 8)

    def test_students_see_only_their_own_feed(self):
        self.prepare()
        ActivityEvent.objects.create(user=self.student, type='LOGIN')
        ActivityEvent.objects.create(user=self.other, type='LECTURE_STARTED')
        feed = self.client.get('/api/v1/me/activity/').data
        self.assertEqual([row['type'] for row in feed], ['PRACTICE_SUBMITTED', 'LECTURE_COMPLETED', 'LECTURE_STARTED'])
        self.assertEqual(self.client.get('/api/v1/admin/activity/').status_code, 403)
        self.assertEqual(self.admin_client.get('/api/v1/me/activity/').status_code, 403)
