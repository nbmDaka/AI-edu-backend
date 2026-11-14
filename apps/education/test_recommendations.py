from django.test import TestCase
from rest_framework.test import APIClient

from apps.assessments.models import TestAttempt
from . import test_adaptive
from .models import LearningItem, LearningTrack
from .recommendations import recommend_materials


class RecommendationTests(TestCase):
    setUp = test_adaptive.AdaptiveLearningTests.setUp
    prepare = test_adaptive.AdaptiveLearningTests.prepare
    submit = test_adaptive.AdaptiveLearningTests.submit
    make_lecture = test_adaptive.AdaptiveLearningTests.make_lecture

    def configure(self):
        for item, key in [(self.lecture, ' Theory '), (self.practice, 'APPLICATION')]:
            response = self.admin_client.patch(f'/api/v1/items/{item.short_id}/', {'competency_keys': [key, key.lower()]}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            item.refresh_from_db()
        self.assertEqual(self.lecture.competency_keys, ['theory'])

    def history(self):
        return self.client.get('/api/v1/attempts/').data['results'][0]['adaptive_result']['recommendations']

    def test_different_weak_competencies_recommend_different_materials(self):
        self.configure()
        for key, material in [('theory', self.lecture), ('application', self.practice)]:
            advice = recommend_materials(self.student, self.first, {key: .2, 'other': .8})
            self.assertEqual(advice['groups'][0]['materials'][0]['item_id'], material.pk)
            self.assertIsNone(advice['fallback'])
        advice = recommend_materials(self.student, self.first, {'theory': .4, 'application': .1})
        self.assertEqual([group['competency'] for group in advice['groups']], ['application', 'theory'])
        self.assertEqual(recommend_materials(self.student, self.first, {'theory': .5})['groups'], [])

    def test_no_links_no_data_and_no_weakness_have_honest_fallback(self):
        self.assertEqual(recommend_materials(self.student, self.first, {'theory': .1})['fallback'], 'unmapped_competencies')
        self.assertEqual(recommend_materials(self.student, self.first, {})['fallback'], 'no_data')
        self.assertEqual(recommend_materials(self.student, self.first, {'theory': .8})['fallback'], 'no_weak_competencies')

    def test_attempts_with_different_weak_competencies_save_different_materials(self):
        self.configure()
        self.prepare()
        first = self.submit(2)['adaptive_result']['recommendations']['groups']
        self.assertEqual([(group['competency'], group['materials'][0]['item_id']) for group in first], [('application', self.practice.pk)])
        self.student = self.other
        self.client.force_login(self.other)
        self.questions.reverse()
        self.prepare()
        second = self.submit(2)['adaptive_result']['recommendations']['groups']
        self.assertEqual([(group['competency'], group['materials'][0]['item_id']) for group in second], [('theory', self.lecture.pk)])

    def test_unpublished_parent_or_lecture_and_unversioned_practice_are_excluded(self):
        self.configure()
        self.lecture.lesson.status = 'DRAFT'
        self.lecture.lesson.save()
        self.assertEqual(recommend_materials(self.student, self.first, {'theory': 0})['groups'][0]['materials'], [])
        LearningItem.objects.filter(pk=self.practice.pk).update(practice_version=None)
        self.assertEqual(recommend_materials(self.student, self.first, {'application': 0})['groups'][0]['materials'], [])
        self.first.is_published = False
        self.first.save()
        self.assertEqual(recommend_materials(self.student, self.first, {'theory': 0})['groups'][0]['materials'], [])

    def test_snapshot_survives_content_and_mapping_changes_and_links_are_current(self):
        self.configure()
        self.prepare()
        attempt = self.submit(0)
        saved = TestAttempt.objects.get(pk=attempt['id']).adaptive_result['recommendations']
        self.assertEqual(saved['reference_score'], .5)
        self.assertEqual(saved['groups'][1]['score'], 0)
        self.assertNotIn('available_item_short_id', saved['groups'][0]['materials'][0])
        original_title = self.lecture.title
        LearningItem.objects.filter(pk=self.lecture.pk).update(title='New title', competency_keys=[])
        theory = next(group for group in self.history()['groups'] if group['competency'] == 'theory')
        self.assertEqual(theory['materials'][0]['title'], original_title)
        self.assertEqual(theory['materials'][0]['available_item_short_id'], self.lecture.short_id)
        LearningItem.objects.filter(pk=self.lecture.pk).update(status='DRAFT')
        theory = next(group for group in self.history()['groups'] if group['competency'] == 'theory')
        self.assertIsNone(theory['materials'][0]['available_item_short_id'])
        self.practice.delete()
        application = next(group for group in self.history()['groups'] if group['competency'] == 'application')
        self.assertIsNone(application['materials'][0]['available_item_short_id'])
        self.assertEqual(TestAttempt.objects.get(pk=attempt['id']).adaptive_result['recommendations'], saved)

    def test_drafts_locked_modules_and_foreign_access_are_excluded(self):
        self.configure()
        LearningItem.objects.filter(pk=self.lecture.pk).update(status='DRAFT')
        self.next_lecture.competency_keys = ['theory']
        self.next_lecture.save()
        self.assertEqual(recommend_materials(self.student, self.first, {'theory': 0})['groups'][0]['materials'], [])
        self.assertEqual(recommend_materials(self.student, self.second, {'theory': 0})['groups'][0]['materials'], [])
        foreign = LearningTrack.objects.create(title='Foreign', is_published=True)
        self.other.learning_track = foreign
        self.other.save()
        self.assertEqual(recommend_materials(self.other, self.first, {'application': 0})['groups'][0]['materials'], [])

    def test_revoked_access_removes_history_links_and_other_users_cannot_read_attempt(self):
        self.configure()
        self.prepare()
        self.submit(0)
        other_client = APIClient()
        other_client.force_login(self.other)
        self.assertEqual(other_client.get('/api/v1/attempts/').data['results'], [])
        self.course.is_published = False
        self.course.save()
        self.assertTrue(all(material['available_item_short_id'] is None for group in self.history()['groups'] for material in group['materials']))

    def test_mapping_validation_and_permissions(self):
        url = f'/api/v1/items/{self.lecture.short_id}/'
        for value in [[''], [' '], ['a' * 101], 'theory', ['a'] * 51, [None]]:
            self.assertEqual(self.admin_client.patch(url, {'competency_keys': value}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'competency_keys': ['theory']}, format='json').status_code, 403)
        self.assertEqual(self.admin_client.patch(f'/api/v1/items/{self.test_item.short_id}/', {'competency_keys': ['theory']}, format='json').status_code, 400)

    def test_recommendations_do_not_change_formula_or_admission(self):
        self.configure()
        self.prepare()
        result = self.submit()['adaptive_result']
        self.assertEqual((result['readiness'], result['competency'], result['module_passed']), (77, .6, True))
        self.assertEqual(result['recommendations']['fallback'], 'no_weak_competencies')
        result = self.submit(2)['adaptive_result']
        self.assertEqual(result['readiness'], 48.3)
        self.assertFalse(result['module_passed'])
        self.assertTrue(result['module_completed'])
        self.assertEqual(result['recommendations']['groups'][0]['competency'], 'application')

    def test_regular_test_has_no_adaptive_recommendations(self):
        self.course.adaptive_learning_enabled = False
        self.course.save()
        self.assertIsNone(self.submit()['adaptive_result'])
