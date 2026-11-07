from django.test import TestCase

from .models import Course
from . import test_adaptive


class ReadinessDashboardTests(TestCase):
    setUp = test_adaptive.AdaptiveLearningTests.setUp
    make_lecture = test_adaptive.AdaptiveLearningTests.make_lecture
    prepare = test_adaptive.AdaptiveLearningTests.prepare
    submit = test_adaptive.AdaptiveLearningTests.submit
    url = '/api/v1/me/readiness/'

    def course_row(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return next(row for row in response.data['courses'] if row['id'] == self.course.pk)

    def test_untested_course_shows_open_first_module_and_locked_second(self):
        course = self.course_row()
        self.assertEqual([(row['status'], row['attempts'], row['readiness']) for row in course['modules']],
                         [('available', 0, None), ('locked', 0, None)])
        self.assertEqual((course['passed_modules'], course['total_modules'], course['average_readiness']), (0, 2, None))
        self.assertEqual(course['competencies'], [])

    def test_history_latest_recommendation_and_competency_profile(self):
        self.prepare()
        self.submit(2)
        self.prepare()
        self.submit()
        course = self.course_row()
        first, second = course['modules']
        self.assertEqual((first['status'], first['attempts'], first['readiness'], first['best_readiness']),
                         ('completed', 2, 79.4, 79.4))
        self.assertEqual([(row['readiness'], row['passed']) for row in first['history']], [(43.5, False), (79.4, True)])
        self.assertEqual(first['recommendation']['action'], 'next_module')
        self.assertEqual(second['status'], 'available')
        self.assertEqual((course['passed_modules'], course['average_readiness']), (1, 79.4))
        self.assertTrue(course['competencies'])
        scores = [row['score'] for row in course['competencies']]
        self.assertEqual(scores, sorted(scores))
        for row in course['competencies']:
            self.assertEqual(row['is_weak'], row['score'] < course['weak_topic_limit'])

    def test_only_own_attempts_and_only_adaptive_courses(self):
        self.prepare()
        self.submit(2)
        self.client.force_login(self.other)
        self.assertEqual(self.course_row()['modules'][0]['attempts'], 0)
        Course.objects.filter(pk=self.course.pk).update(adaptive_learning_enabled=False)
        self.assertEqual(self.client.get(self.url).data['courses'], [])

    def test_admins_are_refused(self):
        self.assertEqual(self.admin_client.get(self.url).status_code, 403)
