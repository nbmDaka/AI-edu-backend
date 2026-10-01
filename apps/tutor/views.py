import json
import logging
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from django.conf import settings
from django.http import StreamingHttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .context import lesson_context

logger = logging.getLogger(__name__)


class TutorUnavailable(APIException):
    status_code = 503
    default_detail = 'Тьютор сейчас недоступен. Попробуйте немного позже.'


class TutorThrottle(UserRateThrottle):
    scope = 'tutor'

    def get_rate(self):
        return settings.AI_TUTOR_RATE


class MessageInput(serializers.Serializer):
    role = serializers.ChoiceField(choices=['user', 'assistant'])
    content = serializers.CharField(max_length=6000)


class ChatInput(serializers.Serializer):
    context_type = serializers.ChoiceField(choices=['lesson', 'item'])
    context_id = serializers.CharField(max_length=10)
    session_id = serializers.UUIDField()
    messages = MessageInput(many=True, min_length=1, max_length=12)

    def validate_messages(self, messages):
        if messages[-1]['role'] != 'user':
            raise serializers.ValidationError('Последнее сообщение должно быть вопросом студента.')
        if sum(len(message['content']) for message in messages) > 30000:
            raise serializers.ValidationError('Диалог слишком длинный. Начните новый диалог.')
        return messages


def upstream_request(path, payload=None, *, body=None, content_type='application/json'):
    if not settings.AI_SERVICE_URL or (not settings.DEBUG and not settings.AI_SERVICE_API_KEY):
        raise TutorUnavailable()
    headers = {'Content-Type': content_type}
    if settings.AI_SERVICE_API_KEY:
        headers['X-API-Key'] = settings.AI_SERVICE_API_KEY
    req = Request(f'{settings.AI_SERVICE_URL}/api/{path}', data=body if body is not None else json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    try:
        return urlopen(req, timeout=settings.AI_SERVICE_TIMEOUT)
    except HTTPError as error:
        status = error.code
        error.close()
        logger.warning('AI service rejected tutor request with HTTP %s', status)
        exc = TutorUnavailable('Лимит запросов к тьютору исчерпан. Попробуйте позже.' if status == 429 else None)
        if status == 429:
            exc.status_code = 429
        raise exc from None
    except (URLError, TimeoutError, OSError):
        logger.warning('AI service connection failed')
        raise TutorUnavailable() from None


class TutorChatView(GenericAPIView):
    serializer_class = ChatInput
    throttle_classes = [TutorThrottle]

    @extend_schema(responses={(200, 'text/event-stream'): str})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lesson, course, content = lesson_context(request.user, data['context_type'], data['context_id'])
        payload = {
            'session_id': f'platform:{request.user.pk}:{lesson.pk}:{data["session_id"]}',
            'messages': data['messages'],
            'course_id': course.short_id,
            'lesson_id': lesson.short_id,
            'lesson_title': lesson.title,
            'lesson_content': content,
            'locateInLecture': True,
            'client_metadata': {'platform': 'ai-edu', 'user_id': str(request.user.pk)},
        }
        upstream = upstream_request('chat', payload)
        if 'text/event-stream' not in upstream.headers.get('Content-Type', ''):
            upstream.close()
            raise TutorUnavailable()

        def stream():
            try:
                # Forward complete SSE lines immediately; a buffered 64KB read delays tokens.
                for line in upstream:
                    yield line
            except (OSError, socket.timeout):
                yield ('event: error\ndata: ' + json.dumps({'error': 'Связь с тьютором прервалась. Повторите запрос.'}, ensure_ascii=False) + '\n\n').encode('utf-8')
            finally:
                upstream.close()

        response = StreamingHttpResponse(stream(), content_type='text/event-stream; charset=utf-8')
        response['Cache-Control'] = 'no-cache, no-transform'
        response['X-Accel-Buffering'] = 'no'
        return response


class VoiceContextInput(serializers.Serializer):
    context_type = serializers.ChoiceField(choices=['lesson', 'item'])
    context_id = serializers.CharField(max_length=10)


class SpeechInput(VoiceContextInput):
    text = serializers.CharField(max_length=10000)
    language = serializers.ChoiceField(choices=['ru'], default='ru')


class TranscribeInput(VoiceContextInput):
    file = serializers.FileField()
    language = serializers.ChoiceField(choices=['ru', 'kk', 'en', 'auto'], default='auto')

    def validate_file(self, value):
        allowed = {'audio/webm', 'audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/m4a'}
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('Аудиозапись должна быть не больше 10 МБ.')
        if value.content_type.split(';')[0] not in allowed:
            raise serializers.ValidationError('Формат аудиозаписи не поддерживается.')
        return value


class TutorSpeechView(GenericAPIView):
    serializer_class = SpeechInput
    throttle_classes = [TutorThrottle]

    @extend_schema(responses={(200, 'audio/wav'): bytes})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lesson_context(request.user, data['context_type'], data['context_id'])
        upstream = upstream_request('tts', {'text': data['text'], 'language': data['language']})
        if not upstream.headers.get('Content-Type', '').startswith('audio/'):
            upstream.close()
            raise TutorUnavailable('Сервис озвучки недоступен.')

        def audio_stream():
            try:
                while chunk := upstream.read(16384):
                    yield chunk
            finally:
                upstream.close()

        response = StreamingHttpResponse(audio_stream(), content_type=upstream.headers['Content-Type'])
        response['Cache-Control'] = 'no-store'
        response['X-Accel-Buffering'] = 'no'
        return response


class TutorTranscribeView(GenericAPIView):
    serializer_class = TranscribeInput
    throttle_classes = [TutorThrottle]

    @extend_schema(responses={200: dict})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lesson_context(request.user, data['context_type'], data['context_id'])
        boundary = uuid4().hex
        content_type = data['file'].content_type.split(';')[0]
        extension = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'mp4', 'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/mpeg': 'mp3', 'audio/m4a': 'm4a'}[content_type]
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{data["language"]}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.{extension}"\r\nContent-Type: {content_type}\r\n\r\n').encode()
        body += data['file'].read() + f'\r\n--{boundary}--\r\n'.encode()
        upstream = upstream_request('transcribe', body=body, content_type=f'multipart/form-data; boundary={boundary}')
        try:
            result = json.loads(upstream.read(100000))
            if not isinstance(result, dict):
                raise ValueError()
            text = result.get('text')
            if not isinstance(text, str):
                raise ValueError()
        except (ValueError, OSError):
            raise TutorUnavailable('Не удалось распознать запись.') from None
        finally:
            upstream.close()
        return Response({'text': text, 'language': result.get('language', data['language'])})
