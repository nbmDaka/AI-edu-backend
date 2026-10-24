from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Test, Question, Option, TestAttempt
from .models import Certificate, Course, LearningTrack, Module, Lesson, LearningItem, UserLearningItemProgress


@override_settings(SECURE_SSL_REDIRECT=False, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProgramJourneyTests(TestCase):
    def setUp(self):
        self.track = LearningTrack.objects.create(title='IT', is_published=True)
        self.course = Course.objects.create(title='Программа ИИ', learning_track=self.track, is_published=True, certificate_enabled=True)
        self.user = User.objects.create_user('journey@example.test', 'Password321!', first_name='Анна', last_name='Иванова', learning_track=self.track)
        self.client = APIClient()
        self.client.force_login(self.user)
        self.materials = []
        for index in range(3):
            module = Module.objects.create(course=self.course, title=f'Модуль {index + 1}', position=index, is_published=True)
            lesson = Lesson.objects.create(module=module, title='Лекция', position=0, status='PUBLISHED')
            lecture = LearningItem.objects.create(module=module, lesson=lesson, title='Лекция', type='LECTURE', position=0, status='PUBLISHED')
            test = Test.objects.create(title='Итоговый тест', passing_percent=70, is_published=True)
            item = LearningItem.objects.create(module=module, test=test, title=test.title, type='TEST', position=1, status='PUBLISHED')
            question = Question.objects.create(test=test, text='Вопрос', position=0, points=1)
            correct = Option.objects.create(question=question, text='Да', position=0, is_correct=True)
            Option.objects.create(question=question, text='Нет', position=1, is_correct=False)
            self.materials.append((module, lecture, item, question, correct))
        self.url = f'/api/v1/courses/{self.course.short_id}/journey/'
        self.issue = f'/api/v1/courses/{self.course.short_id}/certificate/'

    def complete(self, index):
        module, lecture, item, question, correct = self.materials[index]
        self.assertEqual(self.client.patch(f'/api/v1/items/{lecture.short_id}/progress/', {'progress_percent': 100}, format='json').status_code, 200)
        response = self.client.post(f'/api/v1/items/{item.short_id}/attempts/', {'answers': [{'question': question.pk, 'option': correct.pk}]}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_order_is_enforced_and_test_requires_study(self):
        self.assertEqual([row['is_locked'] for row in self.client.get(self.url).data['modules']], [False, True, True])
        next_lecture = self.materials[1][1]
        self.assertEqual(self.client.get(f'/api/v1/items/{next_lecture.short_id}/').status_code, 403)
        item, question, correct = self.materials[0][2:]
        self.assertEqual(self.client.post(f'/api/v1/items/{item.short_id}/attempts/', {'answers': [{'question': question.pk, 'option': correct.pk}]}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.issue, {'completed': True}, format='json').status_code, 400)
        self.complete(0)
        self.assertEqual([row['is_locked'] for row in self.client.get(self.url).data['modules']], [False, False, True])

    def test_hidden_previous_module_cannot_be_bypassed(self):
        module = self.materials[0][0]
        module.is_published = False
        module.save()
        self.assertEqual(self.client.get(f'/api/v1/items/{self.materials[1][1].short_id}/').status_code, 403)
        self.assertFalse(self.client.get(self.url).data['configured'])

    def test_certificate_is_issued_once_and_qr_pdf_are_real(self):
        for index in range(3):
            self.complete(index)
        self.assertTrue(self.client.get(self.url).data['can_issue'])
        response = self.client.post(self.issue)
        self.assertEqual(response.status_code, 201, response.data)
        token = response.data['token']
        self.assertEqual(response.data['hours'], 72)
        self.assertEqual(self.client.post(self.issue).data['token'], token)
        self.assertEqual(Certificate.objects.count(), 1)
        anonymous = APIClient()
        public = anonymous.get(f'/api/v1/certificates/{token}/')
        self.assertEqual(public.status_code, 200)
        self.assertNotIn('email', public.data)
        self.assertEqual(public.data['learner_name'], 'Иванова Анна')
        pdf = anonymous.get(response.data['download_url'])
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(b''.join(pdf.streaming_content).startswith(b'%PDF-'))
        qr = anonymous.get(f'/api/v1/certificates/{token}/qr/')
        self.assertTrue(qr.content.startswith(b'\x89PNG'))
        Certificate.objects.filter(token=token).update(revoked_at=timezone.now())
        self.assertTrue(anonymous.get(f'/api/v1/certificates/{token}/').data['revoked'])
        self.assertEqual(anonymous.get(response.data['download_url']).status_code, 404)

    def test_other_track_custom_denial_and_anonymous_cannot_issue(self):
        self.user.course_access_mode = 'CUSTOM'
        self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.issue).status_code, 404)
        self.user.course_access_mode = 'TRACK_DEFAULT'
        self.user.learning_track = LearningTrack.objects.create(title='Other', is_published=True)
        self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.issue).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(self.issue).status_code, 403)

    def test_progress_flags_alone_cannot_forge_test_result(self):
        for _, lecture, item, _, _ in self.materials:
            for entry in [lecture, item]:
                UserLearningItemProgress.objects.create(user=self.user, learning_item=entry, is_completed=True, progress_percent=100)
        self.assertFalse(self.client.get(self.url).data['can_issue'])
        self.assertEqual(self.client.post(self.issue).status_code, 400)

    def test_registration_may_precede_track_choice(self):
        from django.core import mail
        self.client.logout()
        response = self.client.post('/api/v1/auth/register/', {'email': 'newjourney@example.test', 'password': 'AnotherStrong321!', 'first_name': 'Алия'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['learning_track'])
        token = mail.outbox[-1].body.split('/#/verify-email/')[1].split()[0]
        self.assertEqual(self.client.post('/api/v1/auth/verify-email/', {'token': token}, format='json').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/login/', {'email': 'newjourney@example.test', 'password': 'AnotherStrong321!'}, format='json').status_code, 200)
        response = self.client.patch('/api/v1/auth/me/', {'learning_track': self.track.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
