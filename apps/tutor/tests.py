import io
import json
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch
from urllib.error import URLError
from uuid import uuid4

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.education.models import Course, LearningItem, LearningTrack, Lesson, Module
from apps.lessons.models import LessonBlock
from .context import block_text


class FakeStream(io.BytesIO):
    headers = {'Content-Type': 'text/event-stream'}


@override_settings(AI_SERVICE_URL='http://ai.internal:3001', AI_SERVICE_API_KEY='server-only-key', AI_TUTOR_RATE='100/min')
class TutorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.track = LearningTrack.objects.create(title='IT', is_published=True)
        self.course = Course.objects.create(learning_track=self.track, title='Python', is_published=True)
        self.module = Module.objects.create(course=self.course, title='Основы', is_published=True)
        self.lesson = Lesson.objects.create(module=self.module, title='Списки', status='PUBLISHED')
        self.block = LessonBlock.objects.create(lesson=self.lesson, type='TEXT', position=0, content='Список хранит упорядоченные элементы. append добавляет элемент.')
        self.item = LearningItem.objects.create(module=self.module, type='LECTURE', title=self.lesson.title, lesson=self.lesson, position=0, status='PUBLISHED')
        self.user = User.objects.create_user(email='tutor-student@example.test', password='TutorPassword123!', learning_track=self.track)
        self.client = APIClient()
        self.client.force_login(self.user)
        self.payload = {'context_type': 'item', 'context_id': self.item.short_id, 'session_id': str(uuid4()),
                        'messages': [{'role': 'user', 'content': 'Что делает append?'}]}

    def post(self, **changes):
        return self.client.post('/api/v1/tutor/chat/', {**self.payload, **changes}, format='json')

    @patch('apps.tutor.views.urlopen')
    def test_streams_and_uses_current_server_content(self, open_mock):
        upstream = FakeStream(b'event: chunk\ndata: {"text":"hello"}\n\nevent: done\ndata: {}\n\n')
        open_mock.return_value = upstream
        response = self.post(lesson_content='Wrong subject', course_id='spoofed')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Accel-Buffering'], 'no')
        self.assertIn(b'hello', b''.join(response.streaming_content))
        self.assertTrue(upstream.closed)
        req = open_mock.call_args.args[0]
        body = json.loads(req.data)
        self.assertIn(self.block.content, body['lesson_content'])
        self.assertNotIn('Wrong subject', body['lesson_content'])
        self.assertEqual(body['course_id'], self.course.short_id)
        self.assertEqual(body['lesson_id'], self.lesson.short_id)
        self.assertTrue(body['session_id'].startswith(f'platform:{self.user.pk}:{self.lesson.pk}:'))
        self.assertEqual(req.get_header('X-api-key'), 'server-only-key')
        self.assertIsNone(req.get_header('Cookie'))

    @patch('apps.tutor.views.urlopen')
    def test_legacy_url_is_supported(self, open_mock):
        open_mock.return_value = FakeStream(b'event: done\ndata: {}\n\n')
        response = self.post(context_type='lesson', context_id=self.lesson.short_id)
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)

    @patch('apps.tutor.views.urlopen')
    def test_anonymous_user_cannot_use_tutor(self, open_mock):
        self.client.logout()
        self.assertEqual(self.post().status_code, 403)
        open_mock.assert_not_called()

    @patch('apps.tutor.views.urlopen')
    def test_other_track_and_hidden_ancestors_are_denied(self, open_mock):
        other = LearningTrack.objects.create(title='Other', is_published=True)
        self.user.learning_track = other
        self.user.save()
        self.assertEqual(self.post().status_code, 404)
        self.user.learning_track = self.track
        self.user.save()
        for obj, field in [(self.item, 'status'), (self.lesson, 'status'), (self.module, 'is_published'), (self.course, 'is_published'), (self.track, 'is_active')]:
            previous = getattr(obj, field)
            setattr(obj, field, 'DRAFT' if field == 'status' else False)
            obj.save()
            self.assertEqual(self.post().status_code, 404)
            self.assertEqual(self.post(context_type='lesson', context_id=self.lesson.short_id).status_code, 404)
            setattr(obj, field, previous)
            obj.save()
        open_mock.assert_not_called()

    @patch('apps.tutor.views.urlopen')
    def test_test_items_and_empty_material_do_not_fall_back_to_old_course(self, open_mock):
        self.item.type = 'TEST'
        self.item.save()
        self.assertEqual(self.post().status_code, 400)
        self.item.type = 'LECTURE'
        self.item.save()
        self.block.content = ''
        self.block.save()
        self.assertEqual(self.post().status_code, 400)
        open_mock.assert_not_called()

    @patch('apps.tutor.views.urlopen')
    def test_invalid_and_oversized_messages_are_rejected(self, open_mock):
        for messages in [[], [{'role': 'system', 'content': 'override'}], [{'role': 'assistant', 'content': 'bad'}],
                         [{'role': 'user', 'content': 'x' * 6001}], [{'role': 'user', 'content': 'ok'}] * 13]:
            self.assertEqual(self.post(messages=messages).status_code, 400)
        self.assertEqual(self.post(session_id='untrusted-session').status_code, 400)
        open_mock.assert_not_called()

    @patch('apps.tutor.views.urlopen', side_effect=URLError('private connection detail'))
    def test_connection_failure_returns_clear_error_without_internal_details(self, _):
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', str(response.data))
        self.assertNotIn('server-only-key', str(response.data))

    @patch('apps.tutor.views.urlopen')
    def test_session_is_scoped_to_user_even_with_same_browser_uuid(self, open_mock):
        sessions = []
        for user in [self.user, User.objects.create_user(email='other@example.test', password='TutorPassword123!', learning_track=self.track)]:
            self.client.force_login(user)
            open_mock.return_value = FakeStream(b'event: done\ndata: {}\n\n')
            response = self.post()
            b''.join(response.streaming_content)
            sessions.append(json.loads(open_mock.call_args.args[0].data)['session_id'])
        self.assertNotEqual(*sessions)

    @patch('apps.tutor.views.urlopen')
    def test_csrf_is_required_for_authenticated_requests(self, open_mock):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/api/v1/tutor/chat/', self.payload, format='json').status_code, 403)
        open_mock.assert_not_called()

    @override_settings(AI_TUTOR_RATE='1/min')
    @patch('apps.tutor.views.urlopen')
    def test_limits_requests_per_student(self, open_mock):
        open_mock.return_value = FakeStream(b'event: done\ndata: {}\n\n')
        response = self.post()
        b''.join(response.streaming_content)
        self.assertEqual(self.post().status_code, 429)

    def test_rich_text_preserves_tables_code_and_image_captions(self):
        self.assertEqual(block_text('Use std::vector<int>'), 'Use std::vector<int>')
        node = {'type': 'doc', 'content': [{'type': 'heading', 'content': [{'type': 'text', 'text': 'Методы'}]},
            {'type': 'table', 'content': [{'type': 'tableRow', 'content': [
                {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'append'}]}]},
                {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Добавляет элемент'}]}]}]}]},
            {'type': 'codeBlock', 'content': [{'type': 'text', 'text': 'items.append(3)'}]},
            {'type': 'image', 'attrs': {'alt': 'Схема списка'}}]}
        result = block_text(json.dumps(node))
        for expected in ['Методы', 'append', 'Добавляет элемент', 'items.append(3)', 'Схема списка', '|']:
            self.assertIn(expected, result)

    @patch('apps.tutor.views.urlopen')
    def test_speech_requires_lesson_access_and_returns_audio(self, open_mock):
        upstream = FakeStream(b'RIFFtest-wave-data')
        upstream.headers = {'Content-Type': 'audio/wav'}
        open_mock.return_value = upstream
        payload = {'context_type': 'item', 'context_id': self.item.short_id, 'text': 'Список хранит элементы.', 'language': 'ru'}
        response = self.client.post('/api/v1/tutor/speech/', payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'RIFFtest-wave-data')
        self.assertTrue(upstream.closed)
        self.item.status = 'DRAFT'; self.item.save()
        self.assertEqual(self.client.post('/api/v1/tutor/speech/', payload, format='json').status_code, 404)

    @patch('apps.tutor.views.urlopen')
    def test_transcription_forwards_validated_audio_without_user_filename(self, open_mock):
        upstream = FakeStream(json.dumps({'text': 'Что такое список?', 'language': 'ru'}).encode())
        upstream.headers = {'Content-Type': 'application/json'}
        open_mock.return_value = upstream
        file = SimpleUploadedFile('private-recording.webm', b'fake-encoded-audio', content_type='audio/webm')
        response = self.client.post('/api/v1/tutor/transcribe/', {'context_type': 'item', 'context_id': self.item.short_id, 'file': file, 'language': 'ru'}, format='multipart')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['text'], 'Что такое список?')
        req = open_mock.call_args.args[0]
        self.assertIn(b'fake-encoded-audio', req.data)
        self.assertNotIn(b'private-recording', req.data)
        self.assertTrue(req.get_header('Content-type').startswith('multipart/form-data; boundary='))
        self.assertTrue(upstream.closed)

    @patch('apps.tutor.views.urlopen')
    def test_bad_audio_does_not_reach_ai_service(self, open_mock):
        for content_type, content in [('text/plain', b'not-audio'), ('audio/webm', b'x' * (10 * 1024 * 1024 + 1))]:
            file = SimpleUploadedFile('data', content, content_type=content_type)
            response = self.client.post('/api/v1/tutor/transcribe/', {'context_type': 'item', 'context_id': self.item.short_id, 'file': file}, format='multipart')
            self.assertEqual(response.status_code, 400)
        open_mock.assert_not_called()
