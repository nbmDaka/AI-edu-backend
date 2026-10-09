from django.test import TestCase, SimpleTestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Test, Question, Option, TestAttempt
from .adaptive import calculate_readiness
from .models import (Course, LearningTrack, Module, LearningItem, Lesson,
                     UserLearningItemProgress, UserModuleProgress, PracticeSubmission)


class ReadinessFormulaTests(SimpleTestCase):
    def test_matches_enu_formula_and_keeps_original_scale(self):
        self.assertEqual(calculate_readiness(.80, .75, .20, .80), .6375)
        self.assertEqual(calculate_readiness(1, 1, 0, 1), .85)
        self.assertEqual(calculate_readiness(0, 0, 1, 0), -.15)

    def test_rejects_invalid_indicators(self):
        for value in [-.1, 1.1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                calculate_readiness(value, .5, .5, .5)


class AdaptiveLearningTests(TestCase):
    def setUp(self):
        self.track = LearningTrack.objects.create(title='Track', is_published=True)
        self.course = Course.objects.create(title='Course', learning_track=self.track, is_published=True,
                                             adaptive_learning_enabled=True)
        self.first = Module.objects.create(course=self.course, title='First', position=0, is_published=True)
        self.second = Module.objects.create(course=self.course, title='Second', position=1, is_published=True)
        self.student = User.objects.create_user(email='adaptive@example.test', password='Password1234!', learning_track=self.track)
        self.other = User.objects.create_user(email='adaptive-other@example.test', password='Password1234!', learning_track=self.track)
        self.admin = User.objects.create_user(email='adaptive-admin@example.test', password='Password1234!', role='ADMIN')
        self.client = APIClient()
        self.client.force_login(self.student)
        self.admin_client = APIClient()
        self.admin_client.force_login(self.admin)
        self.lecture = self.make_lecture(self.first, 0)
        self.practice = LearningItem.objects.create(module=self.first, type='PRACTICE', title='Practice',
            position=1, status='PUBLISHED', practice_criteria=['Criterion A', 'Criterion B'])
        self.test = Test.objects.create(title='Final test', is_published=True)
        self.test_item = LearningItem.objects.create(module=self.first, type='TEST', title='Test',
            position=2, status='PUBLISHED', test=self.test)
        self.questions = []
        for index, competency in enumerate(['theory', 'theory', 'application', 'application']):
            question = Question.objects.create(test=self.test, text='Q', position=index, competency=competency)
            correct = Option.objects.create(question=question, text='Correct', position=0, is_correct=True)
            wrong = Option.objects.create(question=question, text='Wrong', position=1, is_correct=False)
            self.questions.append((question, correct, wrong))
        self.next_lecture = self.make_lecture(self.second, 0)
        self.attempt_url = f'/api/v1/items/{self.test_item.short_id}/attempts/'

    def make_lecture(self, module, position):
        lesson = Lesson.objects.create(module=module, title='Lecture', position=position, status='PUBLISHED')
        return LearningItem.objects.create(module=module, type='LECTURE', title='Lecture', position=position,
                                           lesson=lesson, status='PUBLISHED')

    def prepare(self, checks=None):
        self.assertEqual(self.client.patch(f'/api/v1/items/{self.lecture.short_id}/progress/',
            {'progress_percent': 95}, format='json').status_code, 200)
        self.assertEqual(self.client.post(f'/api/v1/items/{self.practice.short_id}/practice/',
            {'checks': checks if checks is not None else [True, True]}, format='json').status_code, 200)

    def submit(self, correct_count=4):
        answers = [{'question': question.pk, 'option': (correct if index < correct_count else wrong).pk}
                   for index, (question, correct, wrong) in enumerate(self.questions)]
        response = self.client.post(self.attempt_url, {'answers': answers, 'readiness': 100}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_locked_module_is_denied_through_all_content_paths(self):
        self.assertEqual(self.client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 403)
        self.assertEqual(self.client.get(f'/api/v1/lessons/{self.next_lecture.lesson.short_id}/').status_code, 403)
        self.assertEqual(self.client.get(f'/api/v1/lessons/{self.next_lecture.lesson.short_id}/blocks/').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/v1/items/{self.next_lecture.short_id}/progress/',
            {'progress_percent': 100}, format='json').status_code, 403)
        module_rows = self.client.get('/api/v1/modules/').data['results']
        self.assertEqual([row['is_locked'] for row in module_rows], [False, True])
        self.assertEqual(self.admin_client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 200)
        test = Test.objects.create(title='Next test', is_published=True)
        test_item = LearningItem.objects.create(module=self.second, type='TEST', title='Next test', position=1, test=test, status='PUBLISHED')
        self.assertEqual(self.client.get(f'/api/v1/items/{test_item.short_id}/test/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/v1/tests/{test.pk}/questions/').status_code, 403)
        self.assertEqual(self.client.post(f'/api/v1/items/{test_item.short_id}/attempts/', {'answers': []}, format='json').status_code, 404)

    def test_attempt_requires_lecture_and_practice_without_creating_results(self):
        self.assertEqual(self.client.post(self.attempt_url, {'answers': []}, format='json').status_code, 400)
        self.client.patch(f'/api/v1/items/{self.lecture.short_id}/progress/', {'progress_percent': 95}, format='json')
        self.assertEqual(self.client.post(self.attempt_url, {'answers': []}, format='json').status_code, 400)
        self.assertFalse(TestAttempt.objects.exists())
        self.assertFalse(UserModuleProgress.objects.exists())

    def test_pass_unlocks_next_module_only_for_this_student(self):
        self.prepare()
        result = self.submit()['adaptive_result']
        self.assertEqual(result['readiness'], 77)
        self.assertEqual(result['competency'], .6)
        self.assertTrue(result['module_passed'])
        self.assertEqual(result['next_item_short_id'], self.next_lecture.short_id)
        self.assertEqual(self.client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 403)

    def test_failure_requires_restudying_and_retains_history(self):
        self.prepare([True, False])
        result = self.submit(2)['adaptive_result']
        self.assertEqual(result['readiness'], 31)
        self.assertFalse(result['module_passed'])
        self.assertEqual(result['repeat_item_short_id'], self.lecture.short_id)
        progress = UserLearningItemProgress.objects.get(user=self.student, learning_item=self.lecture)
        self.assertEqual(progress.progress_percent, 0)
        self.assertFalse(progress.is_completed)
        self.assertFalse(PracticeSubmission.objects.filter(user=self.student).exists())
        self.assertEqual(self.client.post(self.attempt_url, {'answers': []}, format='json').status_code, 400)
        self.prepare()
        success = self.submit()['adaptive_result']
        self.assertEqual(success['competency'], .72)
        self.assertEqual(success['readiness'], 79.4)
        self.assertTrue(success['module_passed'])
        self.assertEqual(TestAttempt.objects.count(), 2)

    def test_threshold_is_admin_only_and_original_scale_is_preserved(self):
        url = f'/api/v1/modules/{self.first.short_id}/'
        self.assertEqual(self.client.patch(url, {'adaptive_threshold': 40}, format='json').status_code, 403)
        for threshold in [39, 81]:
            self.assertEqual(self.admin_client.patch(url, {'adaptive_threshold': threshold}, format='json').status_code, 400)
        self.assertEqual(self.admin_client.patch(url, {'adaptive_threshold': 80}, format='json').status_code, 200)
        self.prepare()
        self.assertFalse(self.submit()['adaptive_result']['module_passed'])
        self.prepare()
        self.assertTrue(self.submit()['adaptive_result']['module_passed'])
        self.assertEqual(self.admin_client.patch(url, {'adaptive_threshold': 80}, format='json').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 200)

    def test_practice_checks_are_validated_and_changed_criteria_require_resubmission(self):
        url = f'/api/v1/items/{self.practice.short_id}/practice/'
        for checks in [[True], [True, True, False], ['true', 'false'], [1, 0]]:
            self.assertEqual(self.client.post(url, {'checks': checks}, format='json').status_code, 400)
        self.prepare()
        self.admin_client.patch(f'/api/v1/items/{self.practice.short_id}/',
            {'practice_criteria': ['New A', 'New B']}, format='json')
        self.assertIsNone(self.client.get(url).data['checks'])
        self.assertEqual(self.client.post(self.attempt_url, {'answers': []}, format='json').status_code, 400)

    def test_nonadaptive_course_keeps_existing_access_and_test_behavior(self):
        self.course.adaptive_learning_enabled = False
        self.course.save()
        self.assertEqual(self.client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 200)
        self.assertIsNone(self.submit()['adaptive_result'])
        self.assertFalse(UserModuleProgress.objects.exists())

    def test_passing_test_percentage_does_not_replace_module_readiness(self):
        self.prepare([False, False])
        attempt = self.submit(3)
        self.assertEqual(attempt['percent'], 75)
        self.assertTrue(attempt['passed'])
        self.assertEqual(attempt['adaptive_result']['readiness'], 35.25)
        self.assertFalse(attempt['adaptive_result']['module_completed'])
        self.assertFalse(UserLearningItemProgress.objects.filter(user=self.student, learning_item=self.test_item, is_completed=True).exists())
        self.assertEqual(self.client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 403)

    def test_completed_module_is_not_relocked_by_a_later_failed_attempt(self):
        self.prepare()
        self.submit()
        attempt = self.submit(0)
        self.assertFalse(attempt['adaptive_result']['module_passed'])
        self.assertTrue(attempt['adaptive_result']['module_completed'])
        self.assertEqual(self.client.get(f'/api/v1/items/{self.next_lecture.short_id}/').status_code, 200)
        self.assertTrue(PracticeSubmission.objects.filter(user=self.student).exists())

    def test_module_without_practice_has_no_unmet_practical_requirements(self):
        self.practice.delete()
        self.client.patch(f'/api/v1/items/{self.lecture.short_id}/progress/', {'progress_percent': 95}, format='json')
        result = self.submit()['adaptive_result']
        self.assertEqual(result['practice'], 1)
        self.assertTrue(result['module_passed'])

    def test_admin_can_configure_competencies_and_invalid_names_are_rejected(self):
        url = f'/api/v1/items/{self.test_item.short_id}/test/'
        payload = {'title': 'Final test', 'is_published': True, 'questions': [{
            'text': 'Question', 'position': 0, 'points': 1, 'competency': 'Основы ИИ',
            'options': [{'text': 'Yes', 'position': 0, 'is_correct': True},
                        {'text': 'No', 'position': 1, 'is_correct': False}],
        }]}
        response = self.admin_client.put(url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['questions'][0]['competency'], 'Основы ИИ')
        self.assertEqual(Question.objects.get(test=self.test).competency, 'Основы ИИ')
        self.assertNotIn('is_correct', str(self.client.get(url).data))
        for name in ['', ' ', 'a' * 101]:
            payload['questions'][0]['competency'] = name
            self.assertEqual(self.admin_client.put(url, payload, format='json').status_code, 400)

    def test_locked_lecture_media_is_not_accessible_by_direct_url(self):
        from apps.mediafiles.models import MediaFile
        from apps.lessons.models import LessonBlock
        media = MediaFile.objects.create(file='unused.png', original_name='unused.png',
            content_type='image/png', size=10, uploaded_by=self.admin)
        LessonBlock.objects.create(lesson=self.next_lecture.lesson, type='IMAGE', position=0, media=media)
        self.assertEqual(self.client.get(f'/api/v1/media/{media.pk}/').status_code, 403)
