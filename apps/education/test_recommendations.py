from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from apps.assessments.models import TestAttempt
from .models import PracticeSubmission, UserLearningItemProgress
from .recommendations import build_recommendation, recommendation_level, review_plan, weak_topics
from . import test_adaptive


def answers(*rows):
    return [(SimpleNamespace(competency=name), correct) for name, correct in rows]


def result(readiness, threshold=60, passed=None, next_item='lecture-2', theory=.5, practice=1, competency=.5):
    return {'readiness': readiness, 'threshold': threshold,
            'module_passed': readiness >= threshold if passed is None else passed,
            'next_item_short_id': next_item, 'theory': theory, 'practice': practice, 'competency': competency}


class RecommendationRulesTests(SimpleTestCase):
    def test_levels_are_measured_from_the_threshold(self):
        self.assertEqual([recommendation_level(gap) for gap in (-30, -15.01, -15, -0.01, 0, 14.99, 15, 25)],
                         ['repeat', 'repeat', 'extra_practice', 'extra_practice', 'next', 'next', 'advanced', 'advanced'])

    def test_readiness_equal_to_threshold_moves_on(self):
        recommendation = build_recommendation(result(60), [])
        self.assertEqual((recommendation['status'], recommendation['action'], recommendation['gap']),
                         ('passed', 'next_module', 0))

    def test_failure_explains_gap_and_weakest_indicator(self):
        recommendation = build_recommendation(result(31, theory=.5, practice=.4, competency=.45), [])
        self.assertEqual(recommendation['status'], 'repeat')
        self.assertEqual(recommendation['action'], 'repeat_module')
        self.assertEqual(recommendation['level'], 'repeat')
        self.assertEqual(recommendation['gap'], -29)
        self.assertEqual(recommendation['weakest_indicator'], 'practice')

    def test_last_module_completes_the_course(self):
        self.assertEqual(build_recommendation(result(90, next_item=None), [])['action'], 'course_completed')

    def test_weak_topics_are_below_sixty_percent_weakest_first(self):
        topics = weak_topics(answers(('Pandas', True), ('pandas ', False), ('Визуализация', False),
                                     ('Регрессия', True), ('Регрессия', True), ('Очистка', True), ('Очистка', False),
                                     ('Очистка', False)))
        self.assertEqual([(row['title'], row['score']) for row in topics],
                         [('Визуализация', 0), ('Очистка', .3333), ('Pandas', .5)])
        self.assertEqual((topics[2]['correct'], topics[2]['total']), (1, 2))


def item(short_id, title, type='LECTURE', position=0, competencies=()):
    return SimpleNamespace(pk=position + 1, short_id=short_id, title=title, type=type, position=position, competencies=list(competencies))


class ReviewPlanTests(SimpleTestCase):
    topics = [{'competency': 'корреляция', 'title': 'Корреляция'}, {'competency': 'pandas', 'title': 'Pandas'}]

    def test_tags_win_over_titles_and_lectures_come_first(self):
        items = [item('pr', 'Практика: корреляция', 'PRACTICE', 0, ['Корреляция']),
                 item('l2', 'Лекция 2', 'LECTURE', 2, [' корреляция ']),
                 item('l1', 'Лекция 1. Корреляция Пирсона', 'LECTURE', 1),
                 item('l3', 'Лекция 3. Pandas', 'LECTURE', 3)]
        plan = review_plan(self.topics, items)
        self.assertEqual([(row['short_id'], row['match']) for row in plan[0]['items']], [('l2', 'tag'), ('pr', 'tag')])
        self.assertEqual([(row['short_id'], row['match']) for row in plan[1]['items']], [('l3', 'title')])

    def test_unmatched_topic_has_no_items_and_list_is_capped(self):
        many = [item(f'l{index}', 'Корреляция', position=index) for index in range(5)]
        plan = review_plan(self.topics, many)
        self.assertEqual(len(plan[0]['items']), 3)
        self.assertEqual(plan[1]['items'], [])


class RecommendationFlowTests(TestCase):
    setUp = test_adaptive.AdaptiveLearningTests.setUp
    make_lecture = test_adaptive.AdaptiveLearningTests.make_lecture
    prepare = test_adaptive.AdaptiveLearningTests.prepare
    submit = test_adaptive.AdaptiveLearningTests.submit

    def test_failed_attempt_stores_a_repeat_recommendation_with_weak_topics(self):
        self.prepare()
        recommendation = self.submit(2)['adaptive_result']['recommendation']
        self.assertEqual(recommendation['status'], 'repeat')
        self.assertEqual(recommendation['action'], 'repeat_module')
        self.assertEqual([row['title'] for row in recommendation['weak_topics']], ['application'])
        self.assertEqual(recommendation['review'], [{'competency': 'application', 'title': 'application', 'items': []}])
        self.assertEqual(TestAttempt.objects.get().adaptive_result['recommendation'], recommendation)

    def test_tagged_lecture_is_recommended_for_its_weak_topic(self):
        self.lecture.competencies = ['Application']
        self.lecture.save(update_fields=['competencies'])
        self.prepare()
        review = self.submit(2)['adaptive_result']['recommendation']['review']
        self.assertEqual(review[0]['items'], [{'short_id': self.lecture.short_id, 'title': 'Lecture', 'type': 'LECTURE', 'match': 'tag'}])

    def test_close_to_threshold_only_weak_topic_materials_are_reopened(self):
        self.lecture.competencies = ['Application']
        self.lecture.save(update_fields=['competencies'])
        self.first.adaptive_threshold = 70
        self.first.save(update_fields=['adaptive_threshold'])
        self.prepare()
        result = self.submit(3)['adaptive_result']
        recommendation = result['recommendation']
        self.assertEqual(recommendation['level'], 'extra_practice')
        self.assertEqual(recommendation['route'], {'scope': 'topics', 'items': [
            {'short_id': self.lecture.short_id, 'title': 'Lecture', 'type': 'LECTURE'}]})
        self.assertEqual(result['repeat_item_short_id'], self.lecture.short_id)
        self.assertFalse(UserLearningItemProgress.objects.get(user=self.student, learning_item=self.lecture).is_completed)
        # The practical of a topic the learner knows stays done, so only the lecture is studied again.
        self.assertTrue(PracticeSubmission.objects.filter(user=self.student, item=self.practice, is_current=True).exists())
        self.assertEqual(self.client.patch(f'/api/v1/items/{self.lecture.short_id}/progress/',
            {'progress_percent': 95}, format='json').status_code, 200)
        self.assertTrue(self.submit()['adaptive_result']['module_passed'])

    def test_far_below_threshold_the_whole_module_is_reopened(self):
        self.lecture.competencies = ['Application']
        self.lecture.save(update_fields=['competencies'])
        self.prepare()
        recommendation = self.submit(2)['adaptive_result']['recommendation']
        self.assertEqual(recommendation['level'], 'repeat')
        self.assertEqual(recommendation['route']['scope'], 'module')
        self.assertEqual([row['short_id'] for row in recommendation['route']['items']],
                         [self.lecture.short_id, self.practice.short_id])
        self.assertFalse(PracticeSubmission.objects.filter(user=self.student, item=self.practice, is_current=True).exists())

    def test_admin_tags_an_item_and_bad_tags_are_refused(self):
        url = f'/api/v1/items/{self.lecture.short_id}/'
        response = self.admin_client.patch(url, {'competencies': [' Pandas ', 'pandas', 'Корреляция']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['competencies'], ['Pandas', 'Корреляция'])
        for bad in (['x' * 101], [''], 'Pandas', [1], [str(n) for n in range(11)]):
            self.assertEqual(self.admin_client.patch(url, {'competencies': bad}, format='json').status_code, 400, bad)

    def test_passed_attempt_recommends_the_next_module(self):
        self.prepare()
        recommendation = self.submit()['adaptive_result']['recommendation']
        self.assertEqual((recommendation['status'], recommendation['action']), ('passed', 'next_module'))
        self.assertEqual(recommendation['weak_topics'], [])
        self.assertEqual(recommendation['gap'], 17)
        self.assertEqual(recommendation['level'], 'advanced')

    def test_admin_analytics_shows_latest_decision_per_student_and_weak_topics(self):
        self.prepare()
        self.submit(2)
        self.client.force_login(self.other)
        self.prepare()
        self.submit(2)
        self.prepare()
        self.submit()
        response = self.admin_client.get('/api/v1/admin/analytics/?period=all')
        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.data['modules'] if row['id'] == self.first.pk)
        self.assertEqual(row['threshold'], 60)
        stats = row['recommendations']
        self.assertEqual((stats['attempts'], stats['tested_students']), (3, 2))
        self.assertEqual((stats['next_students'], stats['repeat_students'], stats['next_rate']), (1, 1, 50))
        self.assertEqual(stats['readiness_values'], [43.5, 79.4])
        self.assertEqual(stats['average_readiness'], 61.45)
        self.assertEqual(stats['weak_topics'], [{'competency': 'application', 'title': 'application', 'students': 1}])
        empty = next(row for row in response.data['modules'] if row['id'] == self.second.pk)['recommendations']
        self.assertEqual((empty['tested_students'], empty['average_readiness'], empty['weak_topics']), (0, None, []))
