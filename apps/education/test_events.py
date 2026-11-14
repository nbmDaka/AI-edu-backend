import io
import json
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase, override_settings
from apps.assessments.models import TestAttempt
from apps.lessons.models import LessonBlock
from . import test_adaptive as fixtures
from .models import LearningEvent, UserCompetencyProgress, UserModuleProgress, UserLearningItemProgress, PracticeSubmission
from apps.tutor.learning_context import learning_context


class LearningEventTests(TestCase):
    setUp = fixtures.AdaptiveLearningTests.setUp
    make_lecture = fixtures.AdaptiveLearningTests.make_lecture
    prepare = fixtures.AdaptiveLearningTests.prepare
    submit = fixtures.AdaptiveLearningTests.submit

    def rows(self, kind):
        return LearningEvent.objects.filter(user=self.student, kind=kind)

    def test_repeat_cycle_is_retained_and_duplicate_completions_are_ignored(self):
        self.prepare()
        self.client.patch(f'/api/v1/items/{self.lecture.short_id}/progress/', {'progress_percent': 100}, format='json')
        submission = PracticeSubmission.objects.get(user=self.student)
        repeat = self.client.post(f'/api/v1/items/{self.practice.short_id}/practice/',
            {'submission_id': submission.pk, 'submission_data': {'checks': [True, True]}}, format='json')
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(self.rows('material_completed').count(), 1)
        self.assertEqual(self.rows('practice_submitted').count(), 1)
        first = self.submit(0)
        reset = self.rows('module_reset').get()
        self.assertEqual(reset.attempt_id, first['id'])
        self.assertEqual(reset.details['submission_ids'], [submission.pk])
        self.assertTrue(reset.details['items'][0]['is_completed'])
        self.prepare()
        self.submit()
        self.assertEqual(self.rows('material_completed').count(), 2)
        self.assertEqual(self.rows('practice_submitted').count(), 2)
        self.assertEqual(self.rows('module_unlocked').get().module_id, self.second.pk)
        reset.refresh_from_db()
        self.assertTrue(reset.details['items'][0]['is_completed'])
        self.assertEqual(list(LearningEvent.objects.order_by('id').values_list('kind', flat=True)),
            ['material_completed', 'practice_submitted', 'test_submitted', 'module_reset',
             'material_completed', 'practice_submitted', 'test_submitted', 'module_unlocked'])

    def test_test_and_module_results_are_independent_and_late_failure_does_not_reset(self):
        self.prepare([False, False])
        first = self.submit(3)
        event = self.rows('test_submitted').get()
        self.assertTrue(event.details['passed'])
        self.assertFalse(event.details['module_passed'])
        self.first.adaptive_threshold = 40
        self.first.save()
        self.prepare()
        second = self.submit(2)
        self.assertFalse(second['passed'])
        self.assertTrue(second['adaptive_result']['module_passed'])
        self.submit(0)
        last = self.rows('test_submitted').first()
        self.assertFalse(last.details['module_passed'])
        self.assertTrue(last.details['module_completed'])
        self.assertEqual(self.rows('module_reset').count(), 1)
        self.assertEqual(self.rows('module_unlocked').count(), 1)

    def test_request_key_replays_failed_attempt_without_second_evaluation(self):
        self.prepare()
        answers = [{'question': q.pk, 'option': wrong.pk} for q, _, wrong in self.questions]
        payload = {'answers': answers, 'request_id': str(uuid4())}
        first = self.client.post(self.attempt_url, payload, format='json')
        scores = list(UserCompetencyProgress.objects.values_list('score', flat=True))
        count = LearningEvent.objects.count()
        second = self.client.post(self.attempt_url, payload, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(LearningEvent.objects.count(), count)
        self.assertEqual(TestAttempt.objects.count(), 1)
        self.assertEqual(list(UserCompetencyProgress.objects.values_list('score', flat=True)), scores)
        self.assertEqual(UserModuleProgress.objects.get().attempts, 1)
        payload['answers'] = []
        self.assertEqual(self.client.post(self.attempt_url, payload, format='json').status_code, 400)
        payload['request_id'] = 'bad'
        self.assertEqual(self.client.post(self.attempt_url, payload, format='json').status_code, 400)

    def test_new_request_key_is_a_real_new_attempt_and_key_is_user_scoped(self):
        self.prepare()
        answers = [{'question': q.pk, 'option': correct.pk} for q, correct, _ in self.questions]
        key = str(uuid4())
        for value in [key, str(uuid4())]:
            self.assertEqual(self.client.post(self.attempt_url, {'answers': answers, 'request_id': value}, format='json').status_code, 201)
        self.client.force_login(self.other)
        self.prepare()
        response = self.client.post(self.attempt_url, {'answers': answers, 'request_id': key}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TestAttempt.objects.count(), 3)
        self.assertEqual(TestAttempt.objects.get(pk=response.data['id']).user_id, self.other.pk)

    def test_journal_is_read_only_owner_scoped_and_admin_can_filter(self):
        self.assertEqual(self.client.get('/api/v1/learning-events/').data['count'], 0)
        self.prepare()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/api/v1/learning-events/?user={self.student.pk}').data['count'], 0)
        self.assertEqual(self.admin_client.get(f'/api/v1/learning-events/?user={self.student.pk}').data['count'], 2)
        self.assertEqual(self.admin_client.get('/api/v1/learning-events/?user=invalid').status_code, 400)
        self.assertEqual(self.admin_client.post('/api/v1/learning-events/', {}).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get('/api/v1/learning-events/').status_code, 403)

    def test_event_failure_rolls_back_attempt_scores_and_reset(self):
        self.prepare()
        with patch('apps.education.events.record_event', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                self.submit(0)
        self.assertFalse(TestAttempt.objects.exists())
        self.assertFalse(UserCompetencyProgress.objects.exists())
        self.assertFalse(UserModuleProgress.objects.exists())
        self.assertTrue(UserLearningItemProgress.objects.get(user=self.student, learning_item=self.lecture).is_completed)

    @override_settings(AI_SERVICE_URL='http://local.invalid', AI_SERVICE_API_KEY='test-key', AI_TUTOR_RATE='100/min')
    def test_ai_failure_does_not_change_progress_or_block_test_submission(self):
        from urllib.error import URLError
        LessonBlock.objects.create(lesson=self.lecture.lesson, type='TEXT', position=0, content='Study material')
        self.prepare()
        count = LearningEvent.objects.count()
        with patch('apps.tutor.views.urlopen', side_effect=URLError('offline')):
            response = self.client.post('/api/v1/tutor/chat/', {'context_type': 'item', 'context_id': self.lecture.short_id,
                'session_id': str(uuid4()), 'messages': [{'role': 'user', 'content': 'Help'}]}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(LearningEvent.objects.count(), count)
        self.assertTrue(self.submit()['adaptive_result']['module_completed'])

    def test_certificate_unlock_can_be_caused_by_recompleting_lecture(self):
        self.course.certificate_enabled = True
        self.course.save()
        self.prepare()
        self.submit()
        UserLearningItemProgress.objects.filter(user=self.student, learning_item=self.lecture).update(
            is_completed=False, progress_percent=0, completed_at=None)
        self.client.patch(f'/api/v1/items/{self.lecture.short_id}/progress/', {'progress_percent': 95}, format='json')
        unlock = self.rows('module_unlocked').first()
        self.assertEqual(unlock.item_id, self.lecture.pk)
        self.assertIsNone(unlock.attempt_id)

    def test_empty_context_does_not_invent_history_and_filters_foreign_profiles(self):
        UserCompetencyProgress.objects.create(user=self.other, course=self.course, competency='theory', score=.99)
        data = learning_context(self.student, self.lecture, self.course)
        self.assertIsNone(data['last_attempt'])
        self.assertEqual(data['competencies'], [])
        self.assertEqual(data['recommendations'], [])
        self.assertEqual(data['current']['status'], 'available')
        self.assertFalse(LearningEvent.objects.exists())

    @override_settings(AI_SERVICE_URL='http://local.invalid', AI_SERVICE_API_KEY='test-key', AI_TUTOR_RATE='100/min')
    @patch('apps.tutor.views.urlopen')
    def test_real_tutor_payload_distinguishes_history_and_current_state_and_rechecks_advice(self, upstream):
        from apps.tutor.tests import FakeStream
        LessonBlock.objects.create(lesson=self.lecture.lesson, type='TEXT', position=0, content='Study material')
        self.lecture.competency_keys = ['theory']
        self.lecture.save()
        self.prepare()
        failed = self.submit(0)
        data = learning_context(self.student, self.lecture, self.course)
        self.assertEqual(data['current']['status'], 'repeat')
        self.assertEqual(data['last_attempt']['id'], failed['id'])
        self.assertEqual(data['recommendations'][0]['item_id'], self.lecture.short_id)
        self.prepare()
        self.submit()
        self.submit(0)
        self.first.adaptive_threshold = 80
        self.first.save()
        upstream.return_value = FakeStream(b'event: done\ndata: {}\n\n')
        response = self.client.post('/api/v1/tutor/chat/', {'context_type': 'item', 'context_id': self.lecture.short_id,
            'session_id': str(uuid4()), 'messages': [{'role': 'user', 'content': 'Help'}],
            'learning_context': {'current': {'is_completed': False}}}, format='json')
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)
        data = json.loads(upstream.call_args.args[0].data)['learning_context']
        self.assertTrue(data['current']['is_completed'])
        self.assertEqual(data['current']['threshold'], 80)
        self.assertEqual(data['last_attempt']['threshold'], 60)
        self.assertFalse(data['last_attempt']['module_passed'])
        self.assertTrue(data['last_attempt']['module_completed'])
        self.lecture.status = 'DRAFT'
        self.lecture.save()
        self.assertEqual(learning_context(self.student, self.practice, self.course)['recommendations'], [])
